from app.database import get_db
from app.folder_utils import folder_path_prefix, normalize_folder, relative_folder_path
from app.sort_utils import artist_sort_key
from app.api.items import update_item_tags


def _folder_item_ids(artist_id: int, folder: str | None):
    db = get_db()
    artist = db.execute("SELECT path FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist:
        return []

    folder = normalize_folder(folder)
    conditions = [
        "artist_id = ?",
        "missing = 0",
        "(media_type IN ('image', 'video', 'source', 'archive') OR is_archive=1)",
    ]
    params = [artist_id]
    if folder:
        prefix = folder_path_prefix(artist["path"], folder)
        conditions.append("substr(replace(file_path, '\\', '/'), 1, ?) = ?")
        params.extend([len(prefix), prefix])

    rows = db.execute(
        f"SELECT id FROM items WHERE {' AND '.join(conditions)} ORDER BY file_path",
        params,
    ).fetchall()
    return [row["id"] for row in rows]


def _new_node(path: str, name: str):
    return {
        "path": path,
        "name": name,
        "item_count": 0,
        "children": [],
    }


def list_folders(artist_id: int):
    db = get_db()
    artist = db.execute("SELECT path FROM artists WHERE id=?", (artist_id,)).fetchone()
    if not artist:
        return _new_node("", "全部文件夹")

    rows = db.execute(
        """
        SELECT file_path FROM items
        WHERE artist_id=? AND missing=0
          AND media_type IN ('image', 'video', 'source', 'archive')
        """,
        (artist_id,),
    ).fetchall()

    root = _new_node("", "全部文件夹")
    by_path = {"": root}
    child_maps = {"": {}}

    for row in rows:
        folder = relative_folder_path(artist["path"], row["file_path"])
        root["item_count"] += 1
        if not folder:
            continue

        current = ""
        for part in folder.split("/"):
            next_path = f"{current}/{part}" if current else part
            if next_path not in by_path:
                node = _new_node(next_path, part)
                by_path[next_path] = node
                child_maps[next_path] = {}
                child_maps[current][part] = node
            by_path[next_path]["item_count"] += 1
            current = next_path

    def attach(node):
        children = list(child_maps[node["path"]].values())
        children.sort(key=lambda n: artist_sort_key(n["name"]))
        node["children"] = children
        for child in children:
            attach(child)
        return node

    return attach(root)


def update_folder_tags(artist_id: int, folder: str | None, tag_ids: list[int], mode: str = "add"):
    item_ids = _folder_item_ids(artist_id, folder)
    if not item_ids:
        return {"updated": 0}
    return update_item_tags(artist_id, item_ids, tag_ids, mode)
