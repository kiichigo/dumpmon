#!/usr/bin/python3

u"""
これはCodmonの連絡帳のやり取りや添付ファイル、出欠連絡や資料室のファイルなどを一括ダウンロードするスクリプトです。
ローカルに保存しておくことでサービス終了後にも子供の連絡帳などがいつでも閲覧できるようにできます。
実行後にSphinxビルド可能なrstファイル形式で保存します。
開発者が利用している園の使われかたに依存しています。
Codmonの仕様を網羅しているわけではありません。

"""

import argparse
from datetime import date, time, datetime, timedelta
import getpass
import gettext
import json
import logging
import os
import os.path as p
import pathlib
import pickle
import re
import requests
import sys
import textwrap
from time import sleep
import urllib
import unicodedata

log = logging.getLogger("dumpmon")

_THISDIR = p.dirname(__file__)

_DATA = p.expanduser("~/Desktop/dumpmon")
_DUMPDIR = p.join(_DATA, "dump")
_DEFAULT_OUTPUTDIR = p.join(_DATA, "output")

_TOP_URL = 'https://ps-api.codmon.com'
_API_URL = _TOP_URL + "/api/v2/parent"

_DEFAULT_CONFIG = {
    # Codmon Login Id
    "id": None,
    "lastFetchedDate": None,
    "dumpdir": None,
    "outputdir": None,
}


class Config(object):

    def __init__(self):
        self.appdatadir = get_appdatadir() / "dumpmon"
        self.fn = p.join(self.appdatadir, "config.json")
        self.conf = _DEFAULT_CONFIG
        self.load()

    def load(self):
        if p.isfile(self.fn):
            self.conf = json.load(open(self.fn, 'r'))
        else:
            self.conf = _DEFAULT_CONFIG

    def save(self):
        json.dump(self.conf, open(self.fn, 'w'), indent=2)

    def __enter__(self):
        self.load()
        return self.data()

    def __exit__(self, exc_type, exc_value, traceback):
        self.save()
        return

    def data(self):
        return self.conf

    def clean(self):
        if p.isfile(self.fn):
            os.remove(self.fn)


class Dumpmon(object):

    def __init__(self, start_date=None, end_date=None, outputdir=None):
        self.s_date = start_date
        self.e_date = end_date
        self.session = requests.Session()
        # create program's directory
        self.appdatadir = get_appdatadir() / "dumpmon"
        self.cookiefile = p.join(self.appdatadir, "cookie.dat")
        self.services_cache = None
        self.children_cache = None
        try:
            self.appdatadir.mkdir(parents=True)
        except FileExistsError:
            pass

        if not p.isdir(_DATA):
            os.mkdir(_DATA)

        if not p.isdir(_DUMPDIR):
            os.makedirs(_DUMPDIR)
        # if not p.isdir(_DEFAULT_OUTPUTDIR):
        #     os.makedirs(_DEFAULT_OUTPUTDIR)

        if outputdir:
            self.outputdir = self.outputdir
        else:
            self.outputdir = _DEFAULT_OUTPUTDIR
        if not p.isdir(self.outputdir):
            os.makedirs(self.outputdir)

    # --- Login

    def testLogin(self):
        self.loadCookie()
        # test
        res = self.session.get(_API_URL + "/parents")
        log.debug("res: %r" % res)
        return res.status_code == 200

    def login(self, useSavedId=True):
        if self.testLogin():
            return
        conf = Config()
        if useSavedId and "id" in conf.data() and conf.data()["id"]:
            id = conf.data()["id"]
        else:
            id = input("login: ")
            with Config() as data:
                data()["id"] = id
        pw = getpass.getpass("password: ")
        loginPayload = {"login_id": id, "login_password": pw}
        res = self.session.post(_API_URL + "/login?__env__=myapp", data=loginPayload)
        if res.status_code == 200:
            self.saveCookie()
        return res

    def saveCookie(self):
        with open(self.cookiefile, 'wb') as f:
            pickle.dump(self.session.cookies, f)

    def loadCookie(self):
        if p.isfile(self.cookiefile):
            with open(p.join(self.appdatadir, "cookie.dat"), 'rb') as f:
                self.session.cookies.update(pickle.load(f))

    # --- session.get util

    def get(self, url, headers=None):
        u""" HTTP GET for Codmon session"""
        log.debug("get: %s" % url)
        defaultHaeders = {
            'User-Agent': 'dumpmon',
        }
        headers = dictmerge(defaultHaeders, (headers or {}))
        res = self.session.get(url, headers=headers)
        if res.status_code != 200:
            raise RuntimeError("%r" % res)
        sleep(1.0)
        return res

    def getJson(self, url):
        res = self.get(url)
        resj = res.json()
        if not resj["success"]:
            raise RuntimeError()
        return resj

    # --- json file handle

    def dumpjson(self, fn, item):
        with open(fn, 'w', encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False, indent=4)

    def loadjson(self, fn):
        with open(fn, 'r', encoding="utf-8") as f:
            return json.load(f)

    # --- fetch services list

    def getServices(self):
        """ codmonのservicesをキャッシュ、ファイル、サーバーから取得の順に返します。

        Returns:
            dict: servises jsonのdata
        """
        # https://ps-api.codmon.com/api/v2/parent/services/?use_image_edge=true&__env__=myapp
        if self.services_cache is not None:
            return self.services_cache
        fn = p.join(_DUMPDIR, "services.json")
        if p.isfile(fn):
            self.services_cache = self.loadjson(fn)
        else:
            self.services_cache = self.fetchServices()
        return self.services_cache

    def fetchServices(self):
        """ codmonのservicesをサーバーから取得して保存します。

        Returns:
            dict: servises jsonのdata
        """
        fn = p.join(_DUMPDIR, "services.json")
        url = _API_URL + "/services"
        resj = self.getJson(url)
        self.dumpjson(fn, resj["data"])
        return resj["data"]

    # --- filter util date range

    def dateRangeTest(self, item):
        u"""
        日付範囲より過去なら-1
        未来なら1
        範囲内なら0
        """
        if self.s_date is None:
            return 0
        if self.e_date is None:
            return 0
        if "display_date" in item:
            item_date = date.fromisoformat(item["display_date"])
        elif "insert_datetime" in item:  # "2022-04-01 15:42:09",
            item_date = date.fromisoformat(item["insert_datetime"].split(" ")[0])
        elif "start_date" in item:
            item_date = date.fromisoformat(item["start_date"])
        elif "publishFromDateTime" in item:  # "2022-04-01T10:40:44Z",
            item_date = date.fromisoformat(item["publishFromDateTime"].split("T")[0])
        else:
            raise RuntimeError('Unknown date key: %r' % item)
        a, b = sorted([self.s_date, self.e_date])
        if item_date < a:
            return -1
        elif item_date > b:
            return 1
        else:
            return 0

    def itemDateTime(self, item):
        if "insert_datetime" in item:
            item_dt = datetime.fromisoformat(item["insert_datetime"])
        elif "update_datetime" in item:
            item_dt = datetime.fromisoformat(item["update_datetime"])
        else:
            raise RuntimeError('Unknown date key: %r' % item)
        return item_dt

    # --- timeline

    def getTimeline(self, service_id, page):
        log.info("timeline: %s %s" % (service_id, page))
        fmt = _API_URL + "/timeline/?listpage=%d&search_type[]=new_all&service_id=%d&current_flag=0&use_image_edge=true&__env__=myapp"
        url = fmt % (int(page), int(service_id))
        return self.getJson(url)

    def iterTimeLineItems(self, service_id, start=1, end=10000):
        for i in range(start, end):
            sleep(0.5)
            resj = self.getTimeline(service_id, i)
            for item in resj["data"]:
                result = self.dateRangeTest(item)
                if result == 1:
                    pass
                elif result == 0:
                    yield item
                elif result == -1:
                    return
            if not resj["next_page"]:
                print("LastPage Detected. Finish: %d" % i)
                return

    def fetchTimeline(self):
        srvs = self.getServices()
        for service_id in srvs.keys():
            tl_fdr = p.join(_DUMPDIR, srvs[service_id]["name"], "timeline")
            if not p.isdir(tl_fdr):
                os.makedirs(tl_fdr)
            for item in self.iterTimeLineItems(service_id):
                if item["timeline_kind"] == "topics":
                    itemname = "%(display_date)s_%(id)s.json" % item
                elif item["timeline_kind"] == "comments":
                    itemname = "%(display_date)s_%(id)s.json" % item
                elif item["timeline_kind"] == "responses":
                    itemname = "%(display_date)s_%(id)s.json" % item
                elif item["timeline_kind"] == "bills":
                    itemname = "%(start_date)s_%(id)s.json" % item
                else:
                    print(item)
                    raise RuntimeError("unknown timeline_kind: %s" % item["timeline_kind"])
                fn = p.join(tl_fdr, itemname)
                self.dumpjson(fn, item)

    def iterDumpedTimeline(self, service_id=None):
        srvs = self.getServices()
        for sid in srvs.keys():
            if service_id and sid != service_id:
                continue
            tl_fdr = p.join(_DUMPDIR, srvs[sid]["name"], "timeline")
            for fn in os.listdir(tl_fdr):
                item = self.loadjson(p.join(tl_fdr, fn))
                yield item

    def downloadTimeline(self):
        log.debug("download")

        def dlFileExists(fdr_name, fn_head):
            for fn in os.listdir(fdr_name):
                if fn.startswith(fn_head):
                    return True

        srvs = self.getServices()
        for sid in srvs.keys():
            log.debug("service: %s" % sid)

            s_fdr = p.join(self.outputdir, srvs[sid]["name"])
            for item in self.iterDumpedTimeline(service_id=sid):
                if self.dateRangeTest(item) != 0:
                    continue
                if "file_url" not in item or item["file_url"] is None:
                    continue
                item_displaydate = date.fromisoformat(item["display_date"])
                fdr_name = "%(YYYY-MM)s attachments" % {"YYYY-MM": item_displaydate.strftime("%Y-%m")}
                log.debug("fdr_name: %s" % fdr_name)
                fdr = p.join(s_fdr, fdr_name)
                if not p.isdir(fdr):
                    os.makedirs(fdr)
                fn_head = ("%(display_date)s [%(title)s]" % item)

                if dlFileExists(fdr, fn_head):
                    log.info("aleady exists. skip download: %s" % fn_head)
                    continue

                url = _TOP_URL + item["file_url"]
                res = self.get(url)
                cd = res.headers['Content-Disposition']
                dl_name = parseContnentDisporition(cd)

                fn = fn_head + " " + dl_name
                with open(p.join(fdr, fn), 'wb') as f:
                    f.write(res.content)

                txt_fn = fn_head + ".txt"
                with open(p.join(fdr, txt_fn), 'w', encoding="utf-8") as f:
                    f.write("\n".join(self.makeNote_simpleContent(item)))

    def fetchAlbum(self, service_id, album_id):
        # https://ps-api.codmon.com/api/v2/parent/albums/49193557?perpage=1000&id=49193557&__env__=myapp

        srvs = self.getServices()
        tl_fdr = p.join(_DUMPDIR, srvs[service_id]["name"], "album")
        if not p.isdir(tl_fdr):
            os.makedirs(tl_fdr)
        fmt = _API_URL + "/albums/%(id)s?perpage=1000&id=%(id)s"
        url = fmt % {"id": album_id}
        item = self.getJson(url)["data"]
        itemname = "%(display_date)s_%(id)s.json" % item
        fn = p.join(tl_fdr, itemname)
        self.dumpjson(fn, item)
        return item

    def downloadTimelinePhoto(self):
        log.debug("download photo")

        def dlFileExists(fdr_name, fn_head):
            for fn in os.listdir(fdr_name):
                if fn.startswith(fn_head):
                    return True

        srvs = self.getServices()
        for sid in srvs.keys():
            log.debug("service: %s" % sid)

            s_fdr = p.join(self.outputdir, srvs[sid]["name"])
            for item in self.iterDumpedTimeline(service_id=sid):
                if self.dateRangeTest(item) != 0:
                    continue
                if "photos" not in item or item["photos"] is None:
                    continue

                item_displaydate = date.fromisoformat(item["display_date"])
                fdr_name = "%(YYYY-MM-DD)s photos" % {"YYYY-MM-DD": item_displaydate.isoformat()}
                log.debug("fdr_name: %s" % fdr_name)
                fdr = p.join(s_fdr, fdr_name)
                if not p.isdir(fdr):
                    os.makedirs(fdr)

                # photos はtimelineにはすべての画像URLが含まれない
                # albumsにアクセスしてjsonを得る
                sub_item = self.fetchAlbum(sid, item["id"])
                for p_item in sub_item["photos"]:
                    url = p_item["url"]
                    res = self.get(url)
                    ext = None
                    if res.headers["content-type"] == "image/jpeg":
                        ext = ".jpg"
                    else:
                        RuntimeError("unknown albam photo content-type: %s" % p_item["id"])
                    name = url.split("?")[0].split("/")[-1]
                    fn = "%(display_date)s_%(id)s_%(p_id)s[%(name)s]%(ext)s" % {
                        "display_date": item_displaydate.isoformat(),
                        "id": sub_item["id"],  # album id
                        "p_id": p_item["id"],
                        "name": name,
                        "ext": ext
                    }
                    if p.exists(p.join(fdr, fn)):
                        log.info("aleady exists. skip download: %s" % fn)
                        continue

                    with open(p.join(fdr, fn), 'wb') as f:
                        f.write(res.content)

    # --- handout

    def getSID(self):
        return self.session.cookies["CODMONSESSID"]

    def getHandoutsPage(self, page=1):
        u""" 資料室のリスト画面相当のデータを取得 """
        fmt = "https://api-reference-room.codmon.com/v1/handouts/forParents?page=%d"
        headers = {"authorization": self.getSID()}
        url = fmt % page
        return self.get(url, headers=headers)

    def getHandout(self, handoutId):
        u""" 各資料データを取得 """
        fmt = "https://api-reference-room.codmon.com/v1/handouts/%(handoutId)s/forParents"
        headers = {"authorization": self.getSID()}
        url = fmt % {"handoutId": handoutId}
        return self.get(url, headers=headers)

    def iterHandsoutsPage(self):
        u""" 資料室のリスト画面をページ事に取得していくイテレータ """
        resj = self.getHandoutsPage().json()
        for handout in resj["handouts"]:
            yield handout
        pages = resj["page"]["totalPages"]
        for page in range(2, pages + 1):
            resj = self.getHandoutsPage(page=page).json()
            for handout in resj["handouts"]:
                yield handout

    def iterHandouts(self):
        """ handouts(資料室) のリストを順に得る 範囲はself.s_date, self.e_dateの範囲 """
        for item in self.iterHandsoutsPage():
            hid = item["handoutId"]
            result = self.dateRangeTest(item)
            if result == 1:
                pass
            elif result == 0:
                yield self.getHandout(hid).json()
            elif result == -1:
                return

    def handoutDumpFolder(self):
        fdr = p.join(_DUMPDIR, "handouts")
        if not p.isdir(fdr):
            os.makedirs(fdr)
        return fdr

    def fetchHandouts(self):
        """ handouts(資料室) のリストを順に保存する 範囲はself.s_date, self.e_dateの範囲 """
        fdr = self.handoutDumpFolder()
        for item in self.iterHandouts():
            isodt = item["publishFromDateTime"]
            disp_date = date.fromisoformat(isodt.split("T")[0])
            itemname = "%(date)s [%(title)s].json" % {"date": disp_date, "title": item["title"]}
            fn = p.join(fdr, itemname)
            self.dumpjson(fn, item)

    def iterDumpedHandouts(self):
        u""" ダンプ済みhandoutを返す 範囲はself.s_date, self.e_dateの範囲 順不同"""
        fdr = self.handoutDumpFolder()
        for fn in os.listdir(fdr):
            item = self.loadjson(p.join(fdr, fn))
            if self.dateRangeTest(item) == 0:
                yield item

    def downloadHandout(self, item):
        fdr = p.join(self.outputdir, "資料室")
        if not p.isdir(fdr):
            os.makedirs(fdr)
        for i, att in enumerate(item["attachments"]):
            url = att["url"]
            itemname = "%(_date)s [%(title)s][%(count)s] %(filename)s" % dict(
                _date=item["publishFromDateTime"].split("T")[0],
                count=i,
                title=item["title"],
                filename=urllib.parse.unquote(att["fileName"]),
            )
            fn = p.join(fdr, itemname)
            if p.isfile(fn):
                log.info("aleady downloaded: %s" % itemname)
                continue
            res = self.get(url)
            with open(fn, 'wb') as f:
                f.write(res.content)

    def downloadAllHandout(self):
        u""" start date, end dateの範囲内のhandoutをダウンロードする """
        for item in self.iterDumpedHandouts():
            self.downloadHandout(item)

    # --- children

    def getChildren(self):
        """
        """
        if self.children_cache is not None:
            return self.children_cache
        url = "https://ps-api.codmon.com/api/v2/parent/children/"
        resj = self.getJson(url)
        self.children_cache = resj
        return resj

    def fetchChildren(self):
        fn = p.join(_DUMPDIR, 'children.json')
        self.dumpjson(fn, self.getChildren())

    def iterCMR(self, service_id=None):
        u"""
        child_member_relationsを得る
        service_idを指定するとそのサービスidに限定する
        """
        chil = self.getChildren()
        for data in chil["data"]:
            for rel in data["child_member_relations"]:
                if service_id and rel["service_id"] != service_id:
                    continue
                yield rel

    def srcIdFromMemId(self, memId):
        u""" member_id から service_id を得る """
        for cmr in self.iterCMR():
            if cmr["member_id"] == memId:
                return cmr["service_id"]

    # -- comments

    def iterComments(self, service_id):
        """
        """
        for cmr in self.iterCMR(service_id):
            o_date = cmr["member_open_date"]
            c_date = cmr["member_close_date"]
            start = self.s_date
            end = self.e_date
            if start is None:
                if c_date:
                    start = date.fromisoformat(c_date)
                else:
                    start = date.today()
            if end is None:
                end = date.fromisoformat(o_date)

            fmt = (
                "https://ps-api.codmon.com/api/v2/parent/comments/"
                "?search_kind=2"
                "&relation_id=%(relation_id)d"
                "&relation_kind=2"
                "&search_start_display_date=%(s_date)s"
                "&search_end_display_date=%(s_date)s"
                "&__env__=myapp"
            )

            for s_date in drange(start, end):
                mem = cmr["member_id"]
                url = fmt % {
                    "relation_id": int(mem),
                    "s_date": s_date.isoformat(),
                }
                resj = self.getJson(url)
                for item in resj["data"]:
                    result = self.dateRangeTest(item)
                    if result == 1:
                        pass
                    elif result == 0:
                        yield item
                    elif result == -1:
                        return

    def fetchComments(self):
        u""" Comments(保護者からの連絡)を取得して保存する
        """
        srvs = self.getServices()
        for service_id in srvs.keys():
            cmt_fdr = p.join(_DUMPDIR, srvs[service_id]["name"], "comments")
            if not p.isdir(cmt_fdr):
                os.makedirs(cmt_fdr)
            for item in self.iterComments(service_id):
                itemname = "%(display_date)s_%(id)s.json" % item
                fn = p.join(cmt_fdr, itemname)
                self.dumpjson(fn, item)

    def iterDumpedComments(self, service_id=None):
        srvs = self.getServices()
        for sid in srvs.keys():
            if service_id and sid != service_id:
                continue
            fdr = p.join(_DUMPDIR, srvs[sid]["name"], "comments")
            for fn in os.listdir(fdr):
                item = self.loadjson(p.join(fdr, fn))
                yield item

    # --- contact_responses

    def iterContactResponses(self, service_id):
        for cmr in self.iterCMR(service_id):
            o_date = cmr["member_open_date"]
            c_date = cmr["member_close_date"]
            start = self.s_date
            end = self.e_date
            if start is None:
                if c_date:
                    start = date.fromisoformat(c_date)
                else:
                    start = date.today()
            if end is None:
                end = date.fromisoformat(o_date)
            fmt = (
                "https://ps-api.codmon.com/api/v2/parent/contact_responses/"
                "?member_id=%(member_id)s"
                "&search_start_display_date=%(s_date)s"
                "&search_end_display_date=%(s_date)s"
                "&search_status_id[]=1"
                "&search_status_id[]=2"
                "&search_status_id[]=3"
                "&perpage=1000"
                "&__env__=myapp")

            for s_date in drange(start, end):
                mem = cmr["member_id"]
                url = fmt % {
                    "member_id": int(mem),
                    "s_date": s_date.isoformat(),
                }
                resj = self.getJson(url)
                for item in resj["data"]:
                    result = self.dateRangeTest(item)
                    if result == 1:
                        pass
                    elif result == 0:
                        yield item
                    elif result == -1:
                        return

    def fetchContactResponses(self, service_id=None):
        u"""_ContactResponses(保護者からの遅刻・欠席連絡)を取得して保存する

        Args:
            service_id (str, optional): サービスを限定したい場合はIDを指定する。 Defaults to None.
        """
        srvs = self.getServices()
        for sid in srvs.keys():
            if service_id and sid != service_id:
                continue
            fdr = p.join(_DUMPDIR, srvs[sid]["name"], "contact_responses")
            if not p.isdir(fdr):
                os.makedirs(fdr)
            for item in self.iterContactResponses(sid):
                itemname = "%(display_date)s_%(id)s.json" % item
                fn = p.join(fdr, itemname)
                self.dumpjson(fn, item)

    def iterDumpedContactResponses(self, service_id=None):
        srvs = self.getServices()
        for sid in srvs.keys():
            if service_id and sid != service_id:
                continue
            fdr = p.join(_DUMPDIR, srvs[sid]["name"], "contact_responses")
            for fn in os.listdir(fdr):
                item = self.loadjson(p.join(fdr, fn))
                yield item

    def iterDumpedTemparture(self, service_id=None):
        srvs = self.getServices()
        for sid in srvs.keys():
            if service_id and sid != service_id:
                continue
            for item in self.iterDumpedTimeline(service_id=sid):
                if "content" not in item:
                    continue
                try:
                    content = json.loads(item["content"])
                except json.JSONDecodeError:
                    continue
                if "tempratures" in content:
                    for tempitem in content["tempratures"]:
                        itemdate = date.fromisoformat(item["display_date"])
                        temptime = time.fromisoformat(tempitem["temprature_time"])
                        tempdatetime = datetime.combine(itemdate, temptime)
                        yield (tempdatetime, tempitem["temprature"])

    # --- Attendances

    def fetchAttendances(self):
        """ 登園時間情報を取得
        """
        # https://ps-api.codmon.com/api/v2/parent/attendances/?start_date=2023-01-01&end_date=2023-01-31&__env__=myapp
        url = _API_URL + "/attendances"
        fn = p.join(_DUMPDIR, "attendances.json")
        resj = self.getJson(url)
        self.dumpjson(fn, resj["data"])

    def loadDumpedAttendances(self):
        fn = p.join(_DUMPDIR, "attendances.json")
        atts = self.loadjson(fn)
        return atts

    # --
    # --- communication notebook ---
    # --

    def makenote(self):
        itemProcMap = {
            "timeline": self.procTimeLineItem,
            "comment": self.procCommentItem,
            "contactresponse": self.procContactResponseItem
        }
        self.fetchAttendances()  # debug
        atts = self.loadDumpedAttendances()
        srvs = self.getServices()
        allLines = {}
        for sid in srvs.keys():
            allLines[sid] = {}

            def getItems(items, category, itemsGetFunc):
                for item in itemsGetFunc(service_id=sid):
                    date_time = self.itemDateTime(item)
                    if "display_date" in item:
                        display_date = date.fromisoformat(item["display_date"])
                    else:
                        display_date = date_time.date()
                    items.append((category, display_date, date_time, item))

            items = []
            getItems(items, "timeline", self.iterDumpedTimeline)
            getItems(items, "comment", self.iterDumpedComments)
            getItems(items, "contactresponse", self.iterDumpedContactResponses)
            # DisplayDateでソートする
            items = sorted(items, key=lambda x: x[1:3])

            # serviceごとのフォルダ
            fdr = p.join(self.outputdir, srvs[sid]["name"])
            if not p.isdir(fdr):
                os.makedirs(fdr)

            # 処理中の日付
            cur_date = None
            for item_src, item_displaydate, item_datetime, item in items:
                yyyymm = item_displaydate.strftime("%Y-%m")
                # month header
                if yyyymm not in allLines[sid]:
                    title = "%s %s" % (srvs[sid]["name"], item_displaydate.strftime("%Y年%m月"))
                    line = "\n%(line)s\n%(title)s\n%(line)s\n" % {"title": title, "line": "=" * width(title)}
                    allLines[sid][yyyymm] = [line]
                # date demiliter
                if cur_date is None or cur_date != item_displaydate:
                    cur_date = item_displaydate
                    title = "%s" % item_displaydate.strftime("%m月%d日")
                    line = "\n%s\n%s\n" % (title, "=" * width(title))
                    allLines[sid][yyyymm].append(line)
                    # 登園時間
                    attLine = self.make_attendance(atts, cur_date)
                    if attLine:
                        allLines[sid][yyyymm].append(attLine)

                itemProcFunc = itemProcMap[item_src]
                lines = itemProcFunc(item)
                if lines:
                    allLines[sid][yyyymm].extend(lines)

            # footer
            for yyyymm in allLines[sid].keys():
                pass

            for yyyymm in allLines[sid].keys():
                fn = "%s note.rst" % yyyymm
                with open(p.join(fdr, fn), 'w', encoding="utf-8") as f:
                    txt = "\n".join(allLines[sid][yyyymm])
                    f.write(txt)
        self.make_index()

    def make_attendance(self, atts, att_date):
        """ 登園時間

        Args:
            atts (list): attendance data from dumped json
            att_date (date): date
        """
        def find_date(atts, att_date):
            s_att_date = att_date.isoformat()
            for att in atts:
                if att["start_date"] == s_att_date:
                    return att

        def toStr(att, key):
            if key in att and att[key]:
                t = time.fromisoformat(att[key])
                return t.strftime("%H:%M")
            else:
                return ""
        att = find_date(atts, att_date)
        if att:
            return "%s 〜 %s" % (toStr(att, "start_time"), toStr(att, "end_time"))

    def make_index(self):
        toc_lines = []
        srvs = self.getServices()
        for sid in srvs.keys():
            sname = srvs[sid]["name"]
            fdr = p.join(self.outputdir, sname)
            for fn in os.listdir(fdr):
                if fn.endswith(" note.rst"):
                    toc_lines.append("    %s/%s\n" % (sname, fn[:-4]))

        tochead = (
            ".. toctree::\n"
            "    :maxdepth: 1\n"
            "    :caption: Contents:\n\n"
        )

        lines = []
        index_file = p.join(self.outputdir, 'index.rst')
        if p.isfile(index_file):
            bInTocTree = False
            bEmptyLine = False
            bEmptyLine2 = False
            for line in open(index_file, 'r', encoding="utf-8").readlines():
                if bInTocTree is False:
                    m = re.match(r'\.\. toctree::', line)
                    if m:
                        bInTocTree = True
                    lines.append(line)
                elif bEmptyLine is False:
                    m = re.match(r'^\s*$', line)
                    if m:
                        bEmptyLine = True
                    lines.append(line)
                elif bEmptyLine2 is False:
                    m = re.match(r'^\s*$', line)
                    if m:
                        bEmptyLine2 = True
                        lines.extend(toc_lines)
                        lines.append(line)
                else:
                    lines.append(line)
            if bEmptyLine is False:
                lines.append(tochead)
            if bEmptyLine2 is False:
                lines.extend(toc_lines)
        else:
            lines.append(tochead)
            lines.extend(toc_lines)
        with open(index_file, 'w', encoding="utf-8") as f:
            f.writelines(lines)

    def makeNote_simpleContent(self, item):
        lines = ["\n"]

        _time = self.itemDateTime(item).strftime("%H:%M")
        title = item["title"] + " " + _time
        lines.append(title)
        lines.append("-" * width(title))

        lines.append(htmlToRst(item["content"]))
        return lines

    def makeNote_renraku(self, item):
        assert item["kind"] == "4"
        indent = " " * 4

        c = json.loads(item["content"])
        memo = re.sub(r"<.*?>", "\n", c["memo"])
        lines = ["\n"]

        lines.append("\n..\n\n")
        for line in json.dumps(c, indent=4, ensure_ascii=False).split("\n"):
            lines.append(indent + ".. " + line)
        lines.append("\n")
        _time = self.itemDateTime(item).strftime("%H:%M")
        title = "\n連絡帳 " + _time
        lines.append("%(title)s\n%(line)s" % {"title": title, "line": "-" * width(title)})

        for line in memo.split("\n"):
            wrappedLines = [x for x in textwrap.wrap(line, width=30) if x]
            lines.extend(wrappedLines)
            lines.append("\n")
        memo = "\n".join(lines)
        mood_ = []
        if "mood_morning" in c:
            mood_.append(indent + "| 朝(%s)" % c["mood_morning"])
        if "mood_afternoon" in c:
            mood_.append(indent + "| 夕(%s)" % c["mood_afternoon"])
        if mood_:
            lines.append("\n機嫌")
            lines.extend(mood_)

        if "sleepings" in c and c["sleepings"]:
            lines.append("\n午睡")
            lines.extend([indent + "| " + x for x in c["sleepings"].split("\n")])
        else:
            lines.append("\n午睡なし")

        ts = []
        for t in c["tempratures"]:
            _time = time.fromisoformat(t["temprature_time"])
            ts.append(indent + "| %s℃ (%s)" % (t["temprature"], _time.strftime("%H:%M")))
        if ts:
            lines.append("\n体温")
            lines.extend(ts)

        return lines

    def procTimeLineItem(self, item):
        kindMap = {
            'bills': {
                None: None,
            },
            'comments': {
                '4': self.makeNote_renraku,  # 連絡帳
            },
            'responses': {
                '3': self.makeNote_simpleContent,  # '遅刻・欠席連絡', '連絡'
                '6': self.makeNote_simpleContent,  # '遅刻・欠席連絡', '遅刻'
                '7': self.makeNote_simpleContent,  # '遅刻・欠席連絡', 'お迎え/延長'
                '8': self.makeNote_simpleContent,  # '遅刻・欠席連絡', '病欠'
            },
            'topics': {
                '1': self.makeNote_simpleContent,  #
                '6': self.makeNote_simpleContent,  # アンケート
                '8': None,  # album
            }
        }
        tk = item["timeline_kind"]
        k = item.get("kind")
        if tk not in kindMap:
            log.warning("Unknown timeline_kind: %s" % tk)
        elif k not in kindMap[tk]:
            log.warning("Unknown kind: %s (%s)" % (k, tk))
        else:
            proc = kindMap[tk].get(k)
            if proc:
                return proc(item)

    def procCommentItem(self, item):
        kind = item["kind"]
        if kind == "2":  # 連絡帳（保護者）
            content = json.loads(item["content"])
            lines = ["\n"]

            _time = self.itemDateTime(item).strftime("%H:%M")
            title = "保護者連絡 " + _time
            lines.append("%(title)s\n%(line)s" % {"title": title, "line": "-" * width(title)})

            indent = " " * 4
            mood_ = []
            if "mood_afternoon" in content:
                mood_.append(indent + "| 夕(%s)" % content["mood_afternoon"])
            if "mood_morning" in content:
                mood_.append(indent + "| 朝(%s)" % content["mood_morning"])
            if mood_:
                lines.append("\n機嫌")
                lines.extend(mood_)

            ev_ = []
            if "evacuation_evening" in content:
                ev_.append("夕 (%s) " % content["evacuation_evening"])
            if "evacuation_morning" in content:
                ev_.append("朝 (%s) " % content["evacuation_morning"])
            if ev_:
                lines.append("\n" + " ".join(["排便"] + ev_))

            def toBlock(txt):
                for line in txt.split("\n"):
                    yield indent + "| " + line
            meal_ = []
            if "meal_evening" in content:
                meal_.append("\n夕食")
                meal_.extend(toBlock(content["meal_evening"]))
            if "meal_morning" in content:
                meal_.append("\n朝食")
                meal_.extend(toBlock(content["meal_morning"]))
            if meal_:
                lines.extend(meal_)

            if "temprature" in content:
                lines.append("\n検温")
                lines.append(indent + "%(temprature_time)s %(temprature)s ℃" % content)

            if "memo" in content:
                lines.append("\n")
                memo = content["memo"]
                for line in memo.split("\n"):
                    lines.append("| %s" % line)
            return lines

        raise RuntimeError("unknown kind: %r" % item)

    def procContactResponseItem(self, item):
        kind = item["kind"]
        if kind == "3":
            pass  # return self.makeNote_simpleContent(item)
        elif kind == "6":  # 遅刻・欠席連絡
            pass  # return self.makeNote_simpleContent(item)
        elif kind == "7":  # '
            pass  # return self.makeNote_simpleContent(item)
        elif kind == "8":  # 病欠
            pass  # return self.makeNote_simpleContent(item)
        elif kind == "9":  # 病欠
            pass  # return self.makeNote_simpleContent(item)
        else:
            raise RuntimeError("unknown kind: %r" % item)

        cons = item["content"].split("\n")
        subtitle = cons[0]
        content = cons[2:]

        lines = ["\n"]
        _time = self.itemDateTime(item).strftime("%H:%M")
        title = item["title"] + " " + subtitle + " " + _time
        lines.append(title)
        lines.append("-" * width(title))
        lines.extend(content)

        return lines


# --- util

def get_appdatadir() -> pathlib.Path:
    """
    Returns a parent directory path
    where persistent application data can be stored.

    # linux: ~/.local/share
    # macOS: ~/Library/Application Support
    # windows: C:/Users/<USER>/AppData/Roaming
    """

    home = pathlib.Path.home()

    if sys.platform == "win32":
        return home / "AppData/Roaming"
    elif sys.platform == "linux":
        return home / ".local/share"
    elif sys.platform == "darwin":
        return home / "Library/Application Support"


def drange(s: date, e: date, includeEndDate: bool = True) -> list:
    """Get a day-by-day date list from start date to end date

    Args:
        s (date): start date
        e (date): end date
        includeEndDate (bool, optional): Whether to include the last day. Defaults to False.

    Returns:
        [date]: _description_
    """
    days = (e - s).days
    step = 1 if days >= 0 else -1
    if includeEndDate:
        days += step
    return [s + timedelta(x) for x in range(0, days, step)]


def parseContnentDisporition(cd):
    log.debug(urllib.parse.unquote(cd))
    fns = re.findall(r'filename\*=([\w-]+)\'\'([\w\.%\(\)\+\-]+)$', cd)
    if len(fns) != 1:
        raise RuntimeError("bad cd: %s" % cd)
    if len(fns[0]) != 2:
        raise RuntimeError("bad cd: %s" % cd)
    return urllib.parse.unquote(fns[0][1])


def dictmerge(d1, d2):
    return {**d1, **d2}


def width(txt: str):
    return sum(2 if unicodedata.east_asian_width(x) in 'FWA' else 1 for x in txt)


def removeTag(txt):
    return re.sub(r"<.*?>", " ", txt, flags=re.MULTILINE | re.DOTALL)


def htmlTableToRstListTable(txt):
    def procTable(m):
        # tableタグのマッチを rstのlist-tableへと変換する
        txt = m.group(1)
        rows = []
        for tr in re.finditer(r"<tr.*?>(.*?)</tr>", txt):
            row = tr.group(1)
            cols = []
            for td in re.finditer(r"<td.*?>(.*?)</td>", row):
                col = td.group(1)
                col = removeTag(col)
                cols.append(col)
            rows.append(cols)
        maxcol = max([len(x) for x in rows])
        indent = " " * 4
        lines = ["\n"]
        lines.append(".. list-table::\n")
        for row in rows:
            for i, col in enumerate(row):
                line = indent + "%s %s %s" % ("*" if i == 0 else " ", "-", col)
                lines.append(line)
            for dummy in range(len(row), maxcol):
                line = indent + " - "
                lines.append(line)
        lines = ["TABLEMARK" + x for x in lines]
        return "\n".join(lines) + "\n"
    return re.sub(r"<table.*?>(.*?)</table>", procTable, txt)


def htmlToRst(txt):
    content = htmlTableToRstListTable(txt)
    # HTMLの一部タグを改行にする
    content = re.sub(r"<br>", "\n", content, flags=re.MULTILINE)
    content = re.sub(r"</(h\d|p)>", "\n", content, flags=re.MULTILINE)
    # その他タグを削除する。
    content = re.sub(r"<.*?>", " ", content, flags=re.MULTILINE | re.DOTALL)
    content = re.sub(r"&nbsp;", " ", content, flags=re.MULTILINE)
    # 3個以上の連続した空白文字を3個にする
    # content = re.sub(r"[\t 　]{3,}", " ", content)
    # 3個以上の連続した改行を2個にする
    content = re.sub(r"\n{3,}", "\n\n", content, flags=re.MULTILINE)
    content = re.sub(r"^[ 　]+", "", content, flags=re.MULTILINE)

    def wrapJoin(m):
        line = m.group(1)
        return "\n".join(textwrap.wrap(line, width=30))
    content = re.sub(r"^(.*?)$", wrapJoin, content)
    content = re.sub(r"^TABLEMARK(.*?)$", r"\1", content, flags=re.MULTILINE)
    return content


def callSphinxSetup(outputdir):
    from sphinx.cmd.quickstart import generate

    opt = {
        'path': outputdir,
        'sep': False,
        'dot': '_',
        'project': 'x',
        'author': 'x', 'version': '',
        'release': '',
        'language': None,
        'suffix': '.rst',
        'master': 'index',
        'extensions': [],
        'makefile': True,
        'batchfile': False
    }
    if not p.exists(p.join(outputdir, "conf.py")):
        generate(opt, overwrite=False, templatedir=None)


def callSphinxBuild(outputdir):
    from sphinx.cmd import make_mode
    # builder, source dir, build dir
    return make_mode.run_make_mode(["html", outputdir, p.join(outputdir, "_build")])


def main():
    """
    コドモンにログインして閲覧できる情報をダウンロードする
    オプションを指定しないと、最後に実行した日までのデータを取得します。

    """

    # --- init argument parse

    parser = argparse.ArgumentParser(
        description="Fetches and dumps codmon data.",
        epilog="Login ID and cookies are stored here: %s" % Dumpmon().appdatadir,
    )

    phase = parser.add_argument_group(title="phase", description="Limit the execution phase")
    phase.add_argument("-f", "--fetch", help="fetch json", action="store_true")
    phase.add_argument("-dl", "--download", help="download attachment file", action="store_true")
    phase.add_argument("-m", "--makenote", help="make communication notebook", action="store_true")
    phase.add_argument("-b", "--builddoc", help="build sphinx document", action="store_true")

    daterange = parser.add_argument_group(title="daterange", description="Fetch Date Range")
    group = daterange.add_mutually_exclusive_group()
    group.add_argument(
        "-a", "--all", action="store_true",
        help="Retrieve all data up to the present day")
    group.add_argument(
        "-d", "--day", type=int,
        help="Retrieve data for a specified number of days up to today")
    group.add_argument(
        "-r", "--range", type=date.fromisoformat,
        nargs=2, metavar=("YYYY-MM-DD", "YYYY-MM-DD"),
        help="Obtain data for a specified date range")

    def dir_path(string):
        if os.path.isdir(string):
            return string
        else:
            raise NotADirectoryError(string)

    phase.add_argument(
        "-dd", "--dumpdir", type=dir_path,
        help="dump directory")
    phase.add_argument(
        "-od", "--outputdir", type=str,
        help="output directory")

    parser.add_argument("-v", "--verbosity", help="increase output verbosity", action="store_true")
    parser.add_argument("-q", "--quiet", help="quietly", action="store_true")

    # do argument parse
    args = parser.parse_args()

    # --- log level

    logging.basicConfig()
    if args.verbosity:
        print("verbosity turned on")
        # rootロガーのレベルをDEBUGにする。requestsなどのログも出ます。
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        log.setLevel(level=logging.INFO)
    if args.quiet:
        log.setLevel(level=logging.WARNING)

    # --- date range

    if args.day:
        s_date = date.today()
        e_date = s_date - timedelta(args.day)
    elif args.range:
        s_date = args.range[0]
        e_date = args.range[1]
    elif args.all:
        s_date = None
        e_date = None
    else:
        with Config() as conf:
            if "lastFetchedDate" in conf:
                s_date = date.today()
                e_date = date.fromisoformat(conf["lastFetchedDate"])
                log.info("Fetches data up to the following dates: %s" % e_date.isoformat())
            else:
                s_date = None
                e_date = None

    # --- phase select

    partialExecutionEnabled = args.fetch or args.download or args.makenote or args.builddoc
    allExecute = not partialExecutionEnabled

    # -- login

    log.debug("debug")
    c = Dumpmon(start_date=s_date, end_date=e_date, outputdir=args.outputdir)
    if not c.testLogin():
        c.login()
        while (not c.testLogin()):
            c.login(useSavedId=False)
    c.saveCookie()
    c.fetchServices()
    c.fetchChildren()

    # --- fetch phase

    if allExecute or args.fetch:
        log.info("fetchTimeline...")
        c.fetchTimeline()
        log.info("fetchComments...")
        c.fetchComments()
        log.info("fetchContactResponses...")
        c.fetchContactResponses()
        log.info("fetchHandouts...")
        c.fetchHandouts()

    # --- download attach file phase

    if allExecute or args.download:
        log.info("download...")
        c.downloadTimeline()
        c.downloadTimelinePhoto()
        c.downloadAllHandout()

    if allExecute:
        with Config() as conf:
            conf["lastFetchedDate"] = s_date.isoformat()
        log.info("save last fetch date: %s" % conf["lastFetchedDate"])

    # --- meke communication notebook phase

    if allExecute or args.makenote:
        log.info("makenote...")
        c.makenote()
    if allExecute or args.builddoc:
        log.info("build document...")
        callSphinxSetup(c.outputdir)
        callSphinxBuild(c.outputdir)


if __name__ == "__main__":
    main()
