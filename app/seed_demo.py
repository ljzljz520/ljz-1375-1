# -*- coding: utf-8 -*-
"""一键灌入演示数据（清空后重建库），跑通：同名两船→候选→双编辑合并，
区间/未定年代、多地点照片、缺失时码、审校、撤稿等。用法：python -m app.seed_demo <数据目录>"""
import os
import sys

from . import db as dbmod
from . import repo


def run(root):
    dbp = os.path.join(root, "hall.db")
    if os.path.exists(dbp):
        os.remove(dbp)
    os.makedirs(root, exist_ok=True)
    src = os.path.join(root, "sources")
    os.makedirs(src, exist_ok=True)
    for f, blob in [("dock.jpg", b"JPEG-DOCK"), ("wave.wav", b"WAV")]:
        p = os.path.join(src, f)
        if not os.path.exists(p):
            open(p, "wb").write(blob)
    dbmod.init_db(dbp)
    c = dbmod.connect(dbp)
    E1, E2 = 1, 2

    # 同名但不同的两条船
    s1 = repo.create_ship(c, E1, "鲁蓬渔12号", "1952年A厂建造")
    s2 = repo.create_ship(c, E2, "鲁蓬渔12号", "1971年B厂重建，后被误记同名")
    repo.add_ship_event(c, E1, s1, "build", "A厂铺龙骨", "1952", "2018-04",
                        source="船厂登记册")
    repo.add_ship_name(c, E1, s1, "鲁蓬渔拾贰号", "1952", "1988",
                       source="老船员回忆")
    repo.add_ship_owner(c, E1, s1, "张老大", "1952..1970")
    repo.add_ship_event(c, E2, s2, "rebuild", "B厂重建下水", "1971-09",
                        "2019", source="县志")
    repo.add_ship_owner(c, E2, s2, "李老大", "1971")

    # 身份候选 + 正反证据（双编辑）
    cid = repo.create_candidate(c, E1, s1, s2, "同名待考")
    repo.add_evidence(c, E2, cid, "same", "轮机型号记录相同")
    repo.add_evidence(c, E1, cid, "different", "建造年代与船厂均不同")

    # 行话（一个区间、一个未定）
    j1 = repo.create_jargon(c, E1, "碰潮", "趁涨潮靠岸卸货", "1950..1975",
                            "2018", "老船长")
    repo.set_jargon_status(c, E1, j1, "approved")
    j2 = repo.create_jargon(c, E1, "喊号子", "起网时统一节奏的号子",
                            None, "2019", "王阿婆")
    repo.set_jargon_status(c, E1, j2, "approved")

    # 两个地点 + 一张照片挂两地
    l1 = repo.create_location(c, E1, "东码头", 30.01, 122.11)
    l2 = repo.create_location(c, E1, "西港湾", 30.02, 122.22)
    aid = repo.create_asset(c, E1, "photo", os.path.join(src, "dock.jpg"),
                            "image/jpeg")
    pid = repo.create_photo(c, E1, aid, "1963年东码头起鱼",
                            "家属书面授权", "1963", "2018-06", [l1, l2])
    repo.set_photo_caption(c, E1, pid, "1963年东码头起鱼", "approved")
    repo.set_photo_license(c, E1, pid, "家属书面授权", "granted")
    repo.set_photo_status(c, E1, pid, "approved")

    # 口述：一段有完整时码，一段缺失时码
    aud = repo.create_asset(c, E1, "audio", os.path.join(src, "wave.wav"))
    iid = repo.create_interview(c, E1, "王阿婆", "2019-07", aud)
    repo.add_segment(c, E1, iid, "那时候天没亮就要出海。", "00:00:10",
                     "00:00:22")
    repo.add_segment(c, E1, iid, "老磁带后半卷没对准时码，内容仍记录。")
    repo.set_interview_consent(c, E1, iid, "granted")
    repo.set_interview_status(c, E1, iid, "approved")

    # 史料陈述：起草→送审→另一编辑通过
    q = repo.create_statement(c, E1, "1956年台风灾情",
                              "八一大台风冲毁东码头三处木桩。",
                              "1956-08-01", "2019-03", ship_id=s1,
                              location_id=l1, source="县志+王阿婆")
    repo.submit_statement(c, E1, q)
    repo.review_statement(c, E2, q, True)
    q2 = repo.create_statement(c, E1, "早年渔行惯例",
                               "渔行记账用木板刻码。", "1920..1949",
                               "2019", source="散记")
    repo.submit_statement(c, E1, q2)
    repo.review_statement(c, E2, q2, True)
    c.commit()
    c.close()
    print("演示数据已写入", dbp)
    print("启动：python -m app.server", root)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "data")
