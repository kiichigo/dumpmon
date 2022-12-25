#!/usr/bin/python3
import requests
import os
import os.path as p
import re
import urllib
import json
import pickle
import getpass
import textwrap
from time import sleep
import sys
import pathlib
import logging

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
_ID_DIR = p.join(_DATA, "id")

_TOP_URL = 'https://ps-api.codmon.com'
_API_URL = _TOP_URL + "/api/v2/parent"


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
        if not p.isdir(_ID_DIR):
            os.mkdir(_ID_DIR)

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
        fmt = _API_URL + "/timeline/?listpage=%d&search_type[]=new_all&service_id=%d&current_flag=0&use_image_edge=true&__env__=myapp" 
        url = fmt % (int(page), int(service_id))
        res = self.session.get(url)
        if res.status_code != 200:
            raise RuntimeError("%r" % res)
        resj = res.json()
        if resj["error"]:
            raise RuntimeError("Error: %r" % res)
        assert(resj["success"])
        return resj

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
        for sid in srvs.keys():
            srvfdr = p.join(_DATA, srvs[sid]["name"])
            if not p.isdir(srvfdr):
                os.makedirs(srvfdr)
            for item in self.iterTimeLineItems(sid):
                itemname = item["id"] + ".json"
                fn = p.join(srvfdr, itemname)
                with open(p.join(_ID_DIR, fn), 'w') as f:
                    json.dump(item, f)

    def getHandouts(self, page=1):
        fmt = "https://api-reference-room.codmon.com/v1/handouts/forParents?page=%d"
        headers = {"authorization": self.getSID()}
        url = fmt % page
        return self.session.get(url, headers=headers)

    def getSID(self):
        return self.session.cookies["CODMONSESSID"]

def getDLFN(item):
    return "%(display_date)s [%(title)s]" % item


def parseContnentDisporition(cd):
    print(urllib.parse.unquote(cd))

    fns = re.findall(r'filename\*=([\w-]+)\'\'([\w\.%\(\)\+\-]+)$', cd)
    if len(fns) != 1:
        raise RuntimeError("bad cd: %s" % cd)

    if len(fns[0]) != 2:
        raise RuntimeError("bad cd: %s" % cd)

    return urllib.parse.unquote(fns[0][1])


def getOutputPath(item, *lst):
    display_date = item["display_date"]
    title = item.get("title", "")

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


def download(session, item):
    if "file_url" not in item:
        return
    if item["file_url"] is None:
        return
    url = _TOP_URL + item["file_url"]
    r = session.get(url)
    sleep(0.5)
    cd = r.headers['Content-Disposition']
    try:
        name = parseContnentDisporition(cd)
    except Exception:
        print(item)
        raise
    fn = getDLFN(item) + " " + name
    with open(getOutputPath(item, fn), 'wb') as f:
        f.write(r.content)


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
    c.dumpTimeline()
    # allpage(c.session, 1)

if __name__ == "__main__":
    main()
