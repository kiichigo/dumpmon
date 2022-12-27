#!/usr/bin/python3

from datetime import date, timedelta 
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

def get_datadir() -> pathlib.Path:

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

_TOP_URL = 'https://ps-api.codmon.com'
_API_URL = _TOP_URL + "/api/v2/parent"

def drange(s, e):
    days = (e-s).days
    return [s + timedelta(x) for x in range(0, days, 1 if days >= 0 else -1)]


class Dumpmon(object):

    def __init__(self):
        self.session = requests.Session()
        self.res = []
        # create program's directory
        self.datadir = get_datadir() / "dumpmon"

        try:
            self.datadir.mkdir(parents=True)
        except FileExistsError:
            pass

        if not p.isdir(_DATA):
            os.mkdir(_DATA)

    # --- Config

    def loadConf(self):
        fn = p.join(self.datadir, "config.json")
        if p.isfile(fn):
            with open(fn, "r") as f:
                conf = json.load(f)
        else:
            conf = {}
        return conf

    def saveConf(self, conf):
        fn = p.join(self.datadir, "config.json")
        with open(fn, "w") as f:
            json.dump(conf, f)
        assert(p.isfile(fn))

    # --- Login

    def login(self):
        conf = self.loadConf()
        if "id" in conf:
            id = conf["id"]
        else:
            id = input("login: ")
            conf["id"] = id
            self.saveConf(conf)
        pw = getpass.getpass("password: ")
        loginPayload = {"login_id": id, "login_password": pw}
        r = self.session.post(_API_URL + "/login?__env__=myapp", data=loginPayload)
        self.res.append(r)
        return r

    def saveCookie(self):
        with open(p.join(self.datadir, "cookie.dat"), 'wb') as f:
            pickle.dump(self.session.cookies, f)

    def loadCookie(self):
        with open(p.join(self.datadir, "cookie.dat"), 'rb') as f:
            self.session.cookies.update(pickle.load(f))

    # --- 

    def get(self, url):
        res = self.session.get(url)
        if res.status_code != 200:
            raise RuntimeError("%r" % res)
        return res

    def getJson(self, url):
        res = self.get(url)
        resj = res.json()
        if not resj["success"]:
            raise RuntimeError()
        return resj["data"]

    def services(self):
        u"""
        https://ps-api.codmon.com/api/v2/parent/services/?use_image_edge=true&__env__=myapp
        """
        url = _API_URL + "/services"
        res = self.session.get(url)
        if res.status_code != 200:
            raise RuntimeError("%r" % res)
        resj = res.json()
        assert(resj["success"])
        return resj["data"]

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
                yield item
            if not resj["next_page"]:
                print("LastPage Detected. Finish: %d" % i)
                return
    
    def dumpTimeline(self):
        srvs = self.services()
        for service_id in srvs.keys():
            tl_fdr = p.join(_DATA, srvs[service_id]["name"], "timeline")
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
                    raise RuntimeError("unknown timeline_kind: %s" %  item["timeline_kind"])
                fn = p.join(tl_fdr, itemname)
                with open(fn, 'w') as f:
                    json.dump(item, f)

    def iterDumpedTimeline(self, service_id):
        srvs = self.services()
        tl_fdr = p.join(_DATA, srvs[service_id]["name"], "timeline")
        for fn in os.listdir(tl_fdr):
            with open(p.join(tl_fdr, fn), "r") as f:
                yield json.load(f)

    def getHandouts(self, page=1):
        fmt = "https://api-reference-room.codmon.com/v1/handouts/forParents?page=%d"
        headers = {"authorization": self.getSID()}
        url = fmt % page
        return self.session.get(url, headers=headers)

    def getSID(self):
        return self.session.cookies["CODMONSESSID"]

    def getChildren(self):
        """
        https://ps-api.codmon.com/api/v2/parent/children/
            ?use_image_edge=true
            &__env__=myapp
        """
        url = "https://ps-api.codmon.com/api/v2/parent/children/"
        return self.getJson(url)

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

    def getComments(self, service_id):
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
            if c_date:
                start = date.fromisoformat(c_date)
            else:
                start = date.today()
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
                    "date": s_date.isoformat(,)
                }
                return self.get(url)



class TimelineItem(object):
    def __init__(self, dumpmon, item):
        self.dumpmon = dumpmon
        self.item = item

    def getOutputPath(self, *lst):
        display_date = self.item["display_date"]
        title = self.item.get("title", "")

        if re.search(r"ねこ|幼児", title):
            outfolder = p.join(_DATA, "etc")
        else:
            m = re.match(r'(\d{4}-\d{2})-\d{2}', display_date)
            if not m:
                raise RuntimeError("invalid display_date: %r" % display_date)
            yyyy_dd = m.group(1)
            outfolder = p.join(_DATA, yyyy_dd)
        if not p.isdir(outfolder):
            os.makedirs(outfolder)
        return p.join(outfolder, *lst)

    def download(self):
        if "file_url" not in self.item:
            return
        if self.item["file_url"] is None:
            return

        try:
            cd = res.headers['Content-Disposition']
            name = parseContnentDisporition(cd)
        except Exception:
            print(self.item)
            raise
        
        fn = ( "%(display_date)s [%(title)s]" % self.item ) + name
        full = self.getOutputPath(self.item, fn)

        if p.isfile(full):
            log.info("aleady exists. skip download: %s" % fn)

        url = _TOP_URL + self.item["file_url"]
        res = self.dumpmon.get(url)
        sleep(0.5)


        with open(full, 'wb') as f:
            f.write(res.content)


def parseContnentDisporition(cd):
    log.debug(urllib.parse.unquote(cd))
    fns = re.findall(r'filename\*=([\w-]+)\'\'([\w\.%\(\)\+\-]+)$', cd)
    if len(fns) != 1:
        raise RuntimeError("bad cd: %s" % cd)
    if len(fns[0]) != 2:
        raise RuntimeError("bad cd: %s" % cd)
    return urllib.parse.unquote(fns[0][1])


def renrakuToText(item):
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

    text = item["display_date"] + "\n\n" + memo + "\n\n" + c["meal"] + "\n" + mood + "\n" + slp + "\n" + temp

    return text


def procRenraku(item):
    text = renrakuToText(item)
    fn = "%(display_date)s [連絡帳].txt" % item
    print(fn)
    with open(getOutputPath(item, fn), "w", encoding="utf-8") as f:
        f.write(text)


def procItem(session, item):
    keys = ['display_date', 'id', 'kind', 'service_id', 'timeline_kind']
    fn = "%s.txt" % item["id"]

    if item["kind"] == "4":
        procRenraku(item)

    if p.isfile(p.join(_DATA, "id", fn)):
        return

    if item["kind"] == "1":  # お知らせ
        download(session, item)
    elif item["kind"] == "4":  # 連絡帳
        procRenraku(item)
    elif item["kind"] in ["6"]:  # アンケート
        print("skip; %s" % item["id"])
        print([item[x] for x in keys])
        return
    elif item["kind"] in ["3", "7"]:
        pass
    elif item["kind"] == "8": # 遅刻・欠席連絡
        pass
    elif item["kind"] in ["9"]:  # 都合欠
        pass 
    else:
        raise RuntimeError("unknown kind: %r" % item)

    with open(p.join(_ID_DIR, fn), 'w') as f:
        json.dump(item, f)


def procPage(session, data):
    for i, item in enumerate(data):
        print("[%d]" % i)
        procItem(session, item)


def allpage(session, sid, start=1, end=1000):
    fmt = "/api/v2/parent/timeline/?listpage=%d&search_type[]=new_all&service_id=%d&current_flag=0&use_image_edge=true&__env__=myapp"
    for i in range(start, end):
        sleep(1)
        url = _TOP_URL + (fmt % (i, sid))
        print(url)
        r = session.get(url)
        if r.status_code != 200:
            raise RuntimeError("%r" % r)
        rj = r.json()

        rj["success"]
        rj["data"]
        if rj["error"]:
            raise RuntimeError("Error: %r" % r)

        for item in rj["data"]:
            procItem(session, item)

        if not rj["next_page"]:
            print("LastPage Detected. Finish: %d" % i)
            break

def templist():
    pass


def main():
    c = Dumpmon()
    try:
        c.loadCookie()
    except:
        c.login()
        c.saveCookie()
    # c.dumpTimeline()
    # allpage(c.session, 1)

if __name__ == "__main__":
    main()
