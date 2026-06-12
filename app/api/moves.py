from app.move_matcher import (
    confirm_move_candidate,
    count_waiting_hash_candidates,
    ignore_move_candidate,
    list_move_candidates,
    list_move_history,
    mark_move_candidate_new,
)


def get_move_candidates(status: str = "pending"):
    return {
        "candidates": list_move_candidates(status),
        "waiting_hash_count": count_waiting_hash_candidates() if status == "pending" else 0,
    }


def get_move_history(status: str | None = None):
    return {"history": list_move_history(status)}


def confirm_candidate(candidate_id: int):
    return confirm_move_candidate(candidate_id)


def candidate_as_new(candidate_id: int):
    return mark_move_candidate_new(candidate_id)


def ignore_candidate(candidate_id: int):
    return ignore_move_candidate(candidate_id)
