"""Local static embeddings (model2vec) over turn passages: the free first-pass ranking before Jev."""
import functools, sqlite3
import numpy as np

REPO, REVISION = "minishlab/potion-retrieval-32M", "6fc8051fab2a1e0ee76689cf08c853792ac285e7"
PASSAGE, PASSAGE_STRIDE = 1200, 1000  # static embeddings average tokens, so short passages keep topics sharp


@functools.cache
def model():
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    from model2vec import StaticModel
    try: path = snapshot_download(REPO, revision=REVISION, local_files_only=True)
    except LocalEntryNotFoundError: path = snapshot_download(REPO, revision=REVISION)  # first run only, ~130 MB
    return StaticModel.from_pretrained(path)


def encode(texts: list[str]) -> np.ndarray:
    v = model().encode(texts, max_length=512)
    return (v / np.linalg.norm(v, axis=1, keepdims=True).clip(1e-9)).astype(np.float16)


def passages(text: str) -> list[tuple[int, str]]:
    return [(i, text[i:i + PASSAGE]) for i in range(0, max(1, len(text) - PASSAGE + PASSAGE_STRIDE), PASSAGE_STRIDE)]


def sync(db: sqlite3.Connection) -> int:
    """Embed sessions that are new, changed, or embedded by another model revision. Returns how many."""
    from .sessions import windows
    stale = db.execute("""select s.id, s.mtime, s.card from sessions s left join emb e on e.session_id = s.id
                          where s.n_turns > 0 and (e.session_id is null or e.model != ? or e.mtime != s.mtime)""", (REVISION,)).fetchall()
    for sid, mtime, card in stale:
        keys, texts = [(-1, 0, 0)], [card]  # row 0 is the session card
        for idx, text in db.execute("select idx, text from turns where session_id = ? order by idx", (sid,)):
            for w, wtext in enumerate(windows(text)):
                for start, chunk in passages(wtext): keys.append((idx, w, start)); texts.append(chunk)
        db.execute("insert or replace into emb values(?,?,?,?,?)",
                   (sid, REVISION, mtime, np.array(keys, np.int32).tobytes(), encode(texts).tobytes()))
    db.commit()
    return len(stale)


def similarities(db: sqlite3.Connection, query: str, sids: list[str]) -> tuple[dict[str, float], dict[tuple[str, int, int], tuple[float, int]]]:
    """Cosine similarity of the query to each session card, and best passage (sim, char offset) per turn window."""
    q = encode([query])[0].astype(np.float32)
    card, win = {}, {}
    for sid, keys, vecs in db.execute(f"select session_id, keys, vecs from emb where session_id in ({','.join('?' * len(sids))})", sids):
        k = np.frombuffer(keys, np.int32).reshape(-1, 3)
        sims = np.frombuffer(vecs, np.float16).reshape(len(k), -1).astype(np.float32) @ q
        card[sid] = float(sims[0])
        for (idx, w, start), s in zip(k[1:].tolist(), sims[1:].tolist()):
            if s > win.get((sid, idx, w), (-2.0, 0))[0]: win[(sid, idx, w)] = (s, start)
    return card, win
