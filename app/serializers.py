"""行记录 -> API/前端字典。统一解析事件时间 JSON。"""
from __future__ import annotations

import json


def _et(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def boat(r):
    return {
        "id": r["id"], "current_name": r["current_name"], "hull_no": r["hull_no"],
        "built_event_time": _et(r["built_event_time"]), "status": r["status"],
        "merged_into_id": r["merged_into_id"], "notes": r["notes"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def boat_name(r):
    return {"id": r["id"], "boat_id": r["boat_id"], "name": r["name"],
            "event_time": _et(r["event_time"]), "note": r["note"],
            "created_at": r["created_at"]}


def boat_owner(r):
    return {"id": r["id"], "boat_id": r["boat_id"], "owner": r["owner"],
            "event_time": _et(r["event_time"]), "note": r["note"],
            "created_at": r["created_at"]}


def boat_event(r):
    return {"id": r["id"], "boat_id": r["boat_id"], "kind": r["kind"],
            "title": r["title"], "event_time": _et(r["event_time"]),
            "detail": r["detail"], "created_at": r["created_at"]}


def person(r):
    return dict(id=r["id"], name=r["name"], role=r["role"], bio=r["bio"],
                created_at=r["created_at"], updated_at=r["updated_at"])


def place(r):
    return dict(id=r["id"], name=r["name"], lat=r["lat"], lng=r["lng"],
                kind=r["kind"], note=r["note"], created_at=r["created_at"])


def photo(r):
    return {
        "id": r["id"], "caption": r["caption"], "license": r["license"],
        "license_ref": r["license_ref"], "asset_path": r["asset_path"],
        "thumb_path": r["thumb_path"], "place_id": r["place_id"],
        "taken_event_time": _et(r["taken_event_time"]),
        "collected_at": r["collected_at"],
        "withdrawn": bool(r["withdrawn"]), "withdrawn_at": r["withdrawn_at"],
        "withdrawn_reason": r["withdrawn_reason"], "created_at": r["created_at"],
    }


def jargon(r):
    return dict(id=r["id"], term=r["term"], pronunciation=r["pronunciation"],
                meaning=r["meaning"], example=r["example"],
                collected_at=r["collected_at"], withdrawn=bool(r["withdrawn"]),
                withdrawn_at=r["withdrawn_at"], withdrawn_reason=r["withdrawn_reason"],
                created_at=r["created_at"], updated_at=r["updated_at"])


def interview(r):
    return dict(id=r["id"], person_id=r["person_id"], title=r["title"],
                audio_path=r["audio_path"], place_id=r["place_id"],
                collected_at=r["collected_at"], withdrawn=bool(r["withdrawn"]),
                withdrawn_at=r["withdrawn_at"], withdrawn_reason=r["withdrawn_reason"],
                created_at=r["created_at"])


def segment(r):
    return dict(id=r["id"], interview_id=r["interview_id"], timecode=r["timecode"],
                timecode_missing=bool(r["timecode_missing"]), seq=r["seq"],
                speaker=r["speaker"], text=r["text"], withdrawn=bool(r["withdrawn"]),
                withdrawn_at=r["withdrawn_at"], withdrawn_reason=r["withdrawn_reason"],
                created_at=r["created_at"])


def statement(r):
    return {
        "id": r["id"], "subject_table": r["subject_table"],
        "subject_id": r["subject_id"], "title": r["title"], "body": r["body"],
        "event_time": _et(r["event_time"]), "collected_at": r["collected_at"],
        "source_type": r["source_type"], "source_ref": r["source_ref"],
        "status": r["status"], "created_by": r["created_by"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def review(r):
    return dict(id=r["id"], statement_id=r["statement_id"], editor_id=r["editor_id"],
                action=r["action"], note=r["note"], created_at=r["created_at"])


def candidate(r):
    return dict(id=r["id"], boat_a=r["boat_a"], boat_b=r["boat_b"],
                relation=r["relation"], confidence=r["confidence"],
                status=r["status"], created_by=r["created_by"],
                created_at=r["created_at"])


def evidence(r):
    return dict(id=r["id"], candidate_id=r["candidate_id"], kind=r["kind"],
                detail=r["detail"], supports=bool(r["supports"]),
                created_by=r["created_by"], created_at=r["created_at"])


def merge(r):
    return dict(id=r["id"], surviving_boat_id=r["surviving_boat_id"],
                absorbed_boat_id=r["absorbed_boat_id"], undone=bool(r["undone"]),
                undone_at=r["undone_at"], created_by=r["created_by"],
                created_at=r["created_at"])


def task(r):
    return dict(id=r["id"], target_table=r["target_table"], target_id=r["target_id"],
                reason=r["reason"], status=r["status"],
                assets=json.loads(r["assets_json"] or "[]"),
                created_by=r["created_by"], created_at=r["created_at"],
                done_at=r["done_at"])


def release(r):
    return dict(id=r["id"], content_hash=r["content_hash"],
                dir=r["dir"], manifest=json.loads(r["manifest_json"]),
                created_at=r["created_at"], state=r["state"], note=r["note"],
                rollback_of=r["rollback_of"])
