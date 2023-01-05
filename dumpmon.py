#!/usr/bin/python3

u"""
これはCodmonの連絡帳のやり取りや添付ファイル、出欠連絡や資料室のファイルなどを一括ダウンロードするスクリプトです。

"""

import argparse
from datetime import date, datetime, timedelta
import getpass
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

log = logging.getLogger()


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


_THISDIR = p.dirname(__file__)

_DATA = p.expanduser("~/Desktop/dumpmon")
_DUMPDIR = p.join(_DATA, "dump")
_OUTPUTDIR = p.join(_DATA, "output")

_TOP_URL = 'https://ps-api.codmon.com'
_API_URL = _TOP_URL + "/api/v2/parent"


def drange(s, e, includeEndDate=False):
    days = (e - s).days
    step = 1 if days >= 0 else -1
    if includeEndDate:
        days += step
    return [s + timedelta(x) for x in range(0, days, step)]


class Dumpmon(object):

    def __init__(self, start_date=None, end_date=None):
        self.s_date = start_date
        self.e_date = end_date
        self.session = requests.Session()
        # create program's directory
        self.appdatadir = get_appdatadir() / "dumpmon"
        self.cookiefile = p.join(self.appdatadir, "cookie.dat")
        self.services_cache = None
        try:
            self.appdatadir.mkdir(parents=True)
        except FileExistsError:
            pass

        if not p.isdir(_DATA):
            os.mkdir(_DATA)

    # --- Config

    def loadConf(self):
        fn = p.join(self.appdatadir, "config.json")
        if p.isfile(fn):
            conf = self.loadjson(fn)
        else:
            conf = {}
        return conf

    def saveConf(self, conf):
        fn = p.join(self.appdatadir, "config.json")
        with open(fn, "w") as f:
            json.dump(conf, f)
        assert p.isfile(fn)

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
        conf = self.loadConf()
        if useSavedId and "id" in conf:
            id = conf["id"]
        else:
            id = input("login: ")
            conf["id"] = id
            self.saveConf(conf)
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
        log.info(url)
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
        u"""
        https://ps-api.codmon.com/api/v2/parent/services/?use_image_edge=true&__env__=myapp
        """
        if self.services_cache is not None:
            return self.services_cache
        url = _API_URL + "/services"
        res = self.session.get(url)
        if res.status_code != 200:
            raise RuntimeError("%r" % res)
        resj = res.json()
        assert resj["success"]
        self.services_cache = resj["data"]
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

    def dumpTimeline(self):
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
                if self.dateRangeTest(item) == 0:
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

            s_fdr = p.join(_OUTPUTDIR, srvs[sid]["name"])
            for item in self.iterDumpedTimeline(service_id=sid):
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
                    f.write(self.makeNote_simpleContent(item))
            
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

    def dumpHandouts(self):
        fdr = self.handoutDumpFolder()
        for item in self.iterHandouts():
            isodt = item["publishFromDateTime"]
            disp_date = date.fromisoformat(isodt.split("T")[0])
            itemname = "%(date)s [%(title)s].json" % {"date": disp_date, "title": item["title"]}
            fn = p.join(fdr, itemname)
            self.dumpjson(fn, item)

    def iterDumpedHandouts(self):
        u""" start date, end dateの範囲内のダンプ済みhandoutを返す 順不同"""
        fdr = self.handoutDumpFolder()
        for fn in os.listdir(fdr):
            item = self.loadjson(p.join(fdr, fn))
            if self.dateRangeTest(item) == 0:
                yield item

    def downloadHandout(self, item):
        fdr = p.join(_OUTPUTDIR, "handouts")
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
        https://ps-api.codmon.com/api/v2/parent/children/
            ?use_image_edge=true
            &__env__=myapp
        """
        url = "https://ps-api.codmon.com/api/v2/parent/children/"
        return self.getJson(url)

    # -- comments

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

    def iterComments(self, service_id):
        """
        https://ps-api.codmon.com/api/v2/parent/comments/
            ?search_kind=2
            &relation_id=
            &relation_kind=2
            &search_start_display_date=2022-12-26
            &search_end_display_date=2022-12-26
            &__env__=myapp
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

    def dumpComments(self):
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
                if self.dateRangeTest(item) == 0:
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
            print("o: %r, c: %r" % (o_date, c_date))
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

    def dumpContactResponses(self, service_id=None):
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
                if self.dateRangeTest(item) == 0:
                    yield item

    # --- communication notebook

    def makenote(self):
        itemObjMap = {
            "timeline": self.procTimeLineItem,
            "comment": self.procCommentItem,
            "contactresponse": self.procContactResponseItem
        }
        srvs = self.getServices()
        for sid in srvs.keys():
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
            items = sorted(items, key=lambda x: x[1:3])
            fdr = p.join(_OUTPUTDIR, srvs[sid]["name"])
            if not p.isdir(fdr):
                os.makedirs(fdr)
            for item_src, item_displaydate, item_datetime, item in items:
                fn = "%(YYYY-MM)s note.txt" % {"YYYY-MM": item_displaydate.strftime("%Y-%m")}
                if p.isfile(p.join(fdr, fn)):
                    os.remove(p.join(fdr, fn))
            cur_date = None
            cur_fn = None
            for item_src, item_displaydate, item_datetime, item in items:
                fn = "%(YYYY-MM)s note.txt" % {"YYYY-MM": item_displaydate.strftime("%Y-%m")}
                if cur_fn is None or cur_fn != fn:
                    cur_fn = fn
                    cur_date = None
                if cur_date is None or cur_date != item_displaydate:
                    cur_date = item_displaydate
                    with open(p.join(fdr, fn), 'a', encoding="utf-8") as f:
                        f.write("\n\n------ [%s] ------\n" % cur_date.isoformat())
                note = itemObjMap[item_src](item)
                if note:
                    with open(p.join(fdr, fn), 'a', encoding="utf-8") as f:
                        f.write(note)
                log.debug("dt: %s %r" % (item_src, item_datetime))

    def makeNote_simpleContent(self, item):
        content = re.sub(r"</(h\d|p|br)>", "\n", item["content"], flags=re.MULTILINE)
        content = re.sub(r"<.*?>", " ", content, flags=re.MULTILINE | re.DOTALL)
        content = re.sub(r"\s{3,}", " ", content)
        content = re.sub(r"\n{3,}", "\n\n", content, flags=re.MULTILINE)
        content = "\n".join(
            "\n".join(textwrap.wrap(line, width=30)) for line in content.split("\n")
        )
        note = (
            "\n--- %(date_time)s\n"
            "* %(title)s\n"
            "%(content)s\n"
        ) % dict(
            date_time=self.itemDateTime(item),
            title=item["title"],
            content=content
        )
        return note

    def makeNote_renraku(self, item):
        assert item["kind"] == "4"
        c = json.loads(item["content"])
        memo = re.sub(r"<.*?>", "\n", c["memo"])
        lines = []
        for line in memo.split("\n"):
            lines.extend(textwrap.wrap(line, width=30))
        memo = "\n".join(lines)

        mood_ = []
        if "mood_morning" in c:
            mood_.append("朝(%s)" % c["mood_morning"])
        if "mood_afternoon" in c:
            mood_.append("夕(%s)" % c["mood_afternoon"])
        if mood_:
            mood = " ".join(["機嫌"] + mood_) + "\n"
        else:
            mood = ""

        if "sleepings" in c:
            slp = "午睡 " + c["sleepings"] + "\n"
        else:
            slp = "午睡なし"
        ts = []
        for t in c["tempratures"]:
            ts.append("%s℃(%s)" % (t["temprature"], t["temprature_time"]))
        temp = "\n".join(ts)

        text = "\n---" + item["insert_datetime"] + "\n\n" + memo + "\n\n" + c["meal"] + "\n" + mood + "\n" + slp + "\n" + temp + "\n"

        return text

    def procTimeLineItem(self, item):
        if "kind" in item:
            kind = item["kind"]
        elif "timeline_kind" in item:
            kind = item["timeline_kind"]
        else:
            kind = None
        if kind == "1":  # お知0らせ
            return self.makeNote_simpleContent(item)
        elif kind == "3":
            pass
        elif kind == "4":  # 連絡帳
            return self.makeNote_renraku(item)
        elif kind == "6":  # アンケート
            return  # self.makeNote_simpleContent(item)
        elif kind == "7":
            pass
        elif kind == "8":  # 遅刻・欠席連絡
            return self.makeNote_simpleContent(item)
        elif kind == "9":  # 都合欠
            return self.makeNote_simpleContent(item)
        elif kind == "bills":
            return ""
        raise RuntimeError("unknown kind: %r" % item)

    def procCommentItem(self, item):
        kind = item["kind"]
        if kind == "2":  # 連絡帳（保護者）
            content = json.loads(item["content"])
            # print(json.dumps(content, indent=4, ensure_ascii=False))
            """
{
    "mood_evening": "良い",
    "evacuation_evening_times": "1",
    "evacuation_evening": "硬便",
    "meal_evening": "カレーライス\nフツウン炊いたご飯に和光堂のカレーをかけて混ぜたもの\n野菜ジュース　食塩無添加\n\n完食　にこにこ機嫌よく食べた",
    "sleep": "22:30",
    "memo": "昨日は第二いちご保育園の一日目を楽しんで過ごせたように思います。\n砂場で遊ぶのも本人にとって初めてのことでした。\n家では祖父母が遊んでくれてニコニコと過ごしました。\n夜はママと一緒
にシャワーを浴びました。\n食欲もあり鼻水も出ていません。\n",
    "wake": "6:30",
    "pool": "〇",
    "meal_morning": "和光堂「角煮チャーハン」　完食\n茹　でにんじん　カリフラワー　ブロッコリー\n冷凍の洋風野菜ミックスを茹でたもの　少しつかみ食べ\n\nよく食べた。",
    "temprature": "37.2",
    "temprature_time": "7:30"
}
"""
            lines = []
            lines.append("\n---" + item["insert_datetime"])

            mood_ = []
            if "mood_afternoon" in content:
                mood_.append("夕(%s)" % content["mood_afternoon"])
            if "mood_morning" in content:
                mood_.append("朝(%s)" % content["mood_morning"])
            if mood_:
                lines.append(" ".join(["機嫌"] + mood_))

            ev_ = []
            if "evacuation_evening" in content:
                ev_.append("夕 (%s) " % content["evacuation_evening"])
            if "evacuation_morning" in content:
                ev_.append("朝 (%s) " % content["evacuation_morning"])
            if ev_:
                lines.append(" ".join(["排便"] + ev_))

            meal_ = []
            if "meal_evening" in content:
                meal_.append("夕食: \n" + content["meal_evening"] + "\n")
            if "meal_morning" in content:
                meal_.append("朝食: \n" + content["meal_morning"] + "\n")
            if meal_:
                lines.append("\n".join(["食事"] + meal_))

            if "temprature" in content:
                lines.append("検温: %(temprature_time)s %(temprature)s ℃" % content)

            if "memo" in content:
                lines.append("%(memo)s" % content)
            return "\n".join(lines) + "\n"

        raise RuntimeError("unknown kind: %r" % item)

    def procContactResponseItem(self, item):
        kind = item["kind"]
        if kind == "3":
            return self.makeNote_simpleContent(item)
        elif kind == "6":  # 遅刻・欠席連絡
            return self.makeNote_simpleContent(item)
        elif kind == "7":  # '
            return self.makeNote_simpleContent(item)
        elif kind == "8":  # 病欠
            return self.makeNote_simpleContent(item)
        elif kind == "9":  # 病欠
            return self.makeNote_simpleContent(item)

        raise RuntimeError("unknown kind: %r" % item)


# class TimelineItem(object):
#     def __init__(self, dumpmon, item):
#         self.dumpmon = dumpmon
#         self.item = item

#     def getOutputPath(self):
#         display_date = self.item["display_date"]
#         title = self.item.get("title", "")

#         if re.search(r"ねこ|幼児", title):
#             outfolder = p.join(_OUTPUTDIR, "etc")
#         else:
#             m = re.match(r'(\d{4}-\d{2})-\d{2}', display_date)
#             if not m:
#                 raise RuntimeError("invalid display_date: %r" % display_date)
#             yyyy_dd = m.group(1)
#             outfolder = p.join(_OUTPUTDIR, yyyy_dd)
#         if not p.isdir(outfolder):
#             os.makedirs(outfolder)
#         return outfolder

#     def download(self):
#         if "file_url" not in self.item:
#             return
#         if self.item["file_url"] is None:
#             return

#         # if p.isfile(full):
#         #    log.info("aleady exists. skip download: %s" % fn)

#         url = _TOP_URL + self.item["file_url"]
#         res = self.dumpmon.get(url)

#         try:
#             cd = res.headers['Content-Disposition']
#             name = parseContnentDisporition(cd)
#         except Exception:
#             print(self.item)
#             raise

#         fn = ("%(display_date)s [%(title)s]" % self.item) + name
#         full = p.join(self.getOutputPath(), fn)
#         with open(full, 'wb') as f:
#             f.write(res.content)


# --- util

def parseContnentDisporition(cd):
    log.debug(urllib.parse.unquote(cd))
    fns = re.findall(r'filename\*=([\w-]+)\'\'([\w\.%\(\)\+\-]+)$', cd)
    if len(fns) != 1:
        raise RuntimeError("bad cd: %s" % cd)
    if len(fns[0]) != 2:
        raise RuntimeError("bad cd: %s" % cd)
    return urllib.parse.unquote(fns[0][1])


def main():
    """
    --all none none
    --day 5 doday, today-5
    --range
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-f", "--fetch", help="fetch json", action="store_true")
    parser.add_argument("-dl", "--download", help="download attachment file", action="store_true")
    parser.add_argument("-m", "--makenote", help="make communication notebook", action="store_true")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-a", "--all", action="store_true")
    group.add_argument(
        "-d", "--day", type=int)
    group.add_argument(
        "-r", "--range", type=date.fromisoformat,
        nargs=2, metavar=("YYYY-MM-DD", "YYYY-MM-DD"))
    parser.add_argument("-v", "--verbosity", help="increase output verbosity", action="store_true")

    args = parser.parse_args()

    if args.verbosity:
        print("verbosity turned on")
        logging.basicConfig(level=logging.DEBUG)
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
        s_date = date.today()
        e_date = s_date - timedelta(7)

    partialExecutionEnabled = args.fetch or args.download or args.makenote
    allExecute = not partialExecutionEnabled

    log.debug("debug")
    c = Dumpmon(start_date=s_date, end_date=e_date)
    if not c.testLogin():
        c.login()
        while (not c.testLogin()):
            c.login(useSavedId=False)
    c.saveCookie()

    # dump json
    if allExecute or args.fetch:
        c.dumpTimeline()
        c.dumpComments()
        c.dumpContactResponses()
        c.dumpHandouts()

    # download attach file
    if allExecute or args.download:
        c.downloadTimeline()
        c.downloadAllHandout()

    # meke communication notebook
    if allExecute or args.makenote:
        c.makenote()


if __name__ == "__main__":
    main()
