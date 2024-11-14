import os
import os.path as p
import re
import json

# piyologのエクスポートファイルがあるフォルダ
datapath = "~/data/piyolog"

sleepings = "~/data/sleepings.json"
sleepData = json.load(open(sleepings))
parmin = 5

# dumpmonのattendances.json
attendfile = "~/dumpmon/dump/attendances.json"
attendData = {}
for item in json.load(open(attendfile)):
    ymd = item["start_date"]
    s = item.get("start_time")
    e = item.get("end_time")
    if not s or not e:
        continue
    attendData[ymd] = [
        [int(x) for x in s.split(":")[:2]],
        [int(x) for x in e.split(":")[:2]],
    ]

datesep_rx = re.compile(r"^\-+$")
# 2021/10/10(日)
date_rx = re.compile(r"^(\d+)/(\d+)/(\d+)\((.*?)\)")
# 04:03   寝る
sleep_start_rx = re.compile(r"(\d\d):(\d\d)\s+寝る")
# 03:50   起きる (6時間30分)  
sleep_end_rx = re.compile(r"(\d\d):(\d\d)\s+起きる")
# 09:50   病院  烏山耳鼻
hospital_rx = re.compile(r'^(\d\d):(\d\d)\s+病院')
jibika_rx = re.compile(r'^(\d\d):(\d\d)\s+病院.*?耳鼻')
# (2歳8か月15日)
birth_rx = re.compile(r"\((\d+.*?日)\)")
# うんち合計
infos_end = re.compile(r'うんち合計 \d+回')

def procfile(fn):
    lines = open(p.join(datapath, fn)).readlines()
    phase = None
    # date_str = None
    date_tuple = None
    periodItems = []
    startItem = None
    fileData = {}
    birth_str = None
    text = []
    markers = []
    for line in lines:
        if datesep_rx.match(line):
            if periodItems and periodItems[-1] is None:
                x = (startItem, (23, 59))
                periodItems[-1] = x
            if periodItems:
                fileData[date_tuple] = (birth_str, periodItems, text, markers)
            phase = "SEP"
            periodItems = []
            text = []
            markers = []
            continue
        if phase == "SEP":
            m = date_rx.match(line)
            date_tuple = (int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))
            if date_tuple == (2023, 11, 27, "月"):
                pass
            # date_str = line[:-1]
            phase = "DATE"
            continue
        if phase == "DATE":
            m = birth_rx.search(line)
            if m:
                birth_str = m.group(1)
                continue
            phase = "BIRTH"
        m = sleep_end_rx.match(line)
        if m:
            if phase == "BIRTH":  # not started in a day
                x = ((0, 0), (int(m.group(1)), int(m.group(2))))
                periodItems.append(x)
                continue
            if phase == "START":
                assert (startItem is not None)
                x = (startItem, (int(m.group(1)), int(m.group(2))))
                if periodItems:
                    periodItems[-1] = x
                else:
                    periodItems.append(x)
                phase = "END"
            continue
        m = sleep_start_rx.match(line)
        if m:
            phase = "START"
            startItem = (int(m.group(1)), int(m.group(2)))
            if periodItems and periodItems[-1] is not None:
                periodItems.append(None)
            continue
        m = hospital_rx.match(line)
        m2 = jibika_rx.match(line)
        if m2:
            markers.append(('jibika', int(m.group(1)), int(m.group(2))))
        elif m:
            markers.append(('hospital', int(m.group(1)), int(m.group(2))))
        m = infos_end.match(line)
        if m:
            phase = "TEXT"
            continue
        if phase == "TEXT":
            text.append(line)
    return fileData

def getCodmonPeriod(day):
    ymd = "%04d-%02d-%02d" % day[0:3]
    if ymd in sleepData:
        for rng in sleepData[ymd]:
            s = (int(rng[0][0]), int(rng[0][1]))
            e = (int(rng[1][0]), int(rng[1][1]))
            yield (s, e)

def getAttendPeriod(day):
    ymd = "%04d-%02d-%02d" % day[0:3]
    if ymd in attendData:
        return attendData.get(ymd)

def main():
    allData = {}
    for f in [x for x in os.listdir(datapath) if x.endswith(".txt")]:
        fileData = procfile(f)
        allData.update(fileData)

    def flagPerMin(periodItems, cPeriodItems, attendItem, markers):
        """ parminごとに時間を進めてflagリストを返す """
        for i in range(int(60*24 / parmin)):
            m = i * parmin
            isIn = " "
            for period in periodItems:
                (sh, sm), (eh, em) = period
                s = sh * 60 + sm
                e = eh * 60 + em
                if s <= m and m <= e:
                    isIn = "-"
                    break
            for period in cPeriodItems:
                (sh, sm), (eh, em) = period
                s = sh * 60 + sm
                e = eh * 60 + em
                if s <= m and m <= e:
                    isIn = "x"
                    break
            if attendItem:
                (sh, sm), (eh, em) = attendItem
                s = sh * 60 + sm
                e = eh * 60 + em
                if s <= m and m <= e:
                    isIn += "t"
            for marker in markers:
                text, mh, mm = marker
                m_min = mh * 60 + mm
                if m_min - parmin < m and m <= m_min:
                    isIn += text[0]
            yield isIn


    with open("sllep.csv", "w") as f:
        head = ["", "", ""]
        for i in range(int(60*24/parmin)):
            h = int(i * parmin / 60)
            head.append("%d" % h if ((i * 5) % int(60/parmin)) == 0 else "")
        f.write(",".join(head) + "\n")

        nightSleepStarts = []
        # 就寝時間の7日平均
        for day in sorted(allData.keys()):
            birth_str, periodItems, text, markers = allData[day]
            lastPeriod = sorted(periodItems)[-1]
            (sh, sm), (eh, em) = lastPeriod
            s = sh * 60 + sm if sh > 12 else 24 * 60
            nightSleepStarts.append(s)

        for i, day in enumerate(sorted(allData.keys())):
            birth_str, periodItems, text, markers = allData[day]
            pre7 = nightSleepStarts[:i][-7:]
            if pre7:
                sleep_ave = sum(pre7) / len(pre7)
                markers.append(("ave", int(sleep_ave / 60), sleep_ave%60))
            cPeriodItems = list(getCodmonPeriod(day))
            attemdItem = getAttendPeriod(day)
            flags = list(flagPerMin(periodItems, cPeriodItems, attemdItem, markers))
            if birth_str.endswith("月0日"):
                dayCol = "%04d/%02d/%02d(%s)" % day
                birthCol = birth_str
            else:
                dayCol = ""
                birthCol = ""
            text = '"%s"' % "\n".join(text[1:])
            csvline = ",".join([dayCol, birthCol, str(day[2]) ] + flags)
            f.write(csvline + "\n")


if __name__ == "__main__":
    main()
