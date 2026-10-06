"""Download a SMALL sample of the HF telecom corpus (the full CSV is ~715 MB).

We HTTP range-request the first few MB, parse complete conversations, drop the last one
(probably cut off), and save N conversations to data/corpus_sample.json.
Format of the source: one row per utterance: conversation_id, speaker (agent|client), date_time, text.
"""
import csv
import io
import json
import sys
from pathlib import Path

import httpx

URL = "https://huggingface.co/datasets/talkmap/telecom-conversation-corpus/resolve/main/telecom_200k.csv"
OUT = Path(__file__).with_name("corpus_sample.json")


def main(n_conversations: int = 25, n_bytes: int = 1_500_000) -> None:
    resp = httpx.get(URL, headers={"Range": f"bytes=0-{n_bytes}"}, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    text = resp.content.decode("utf-8", errors="ignore")
    text = text[: text.rfind("\n")]
    convs: dict[str, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        convs.setdefault(row["conversation_id"], []).append(
            {"speaker": row["speaker"], "text": row["text"], "date_time": row["date_time"]})
    ids = list(convs)[:-1]
    sample = [{"conversation_id": cid, "messages": convs[cid]} for cid in ids[:n_conversations]]
    OUT.write_text(json.dumps(sample, indent=1))
    lengths = [len(c["messages"]) for c in sample]
    print(f"saved {len(sample)} conversations to {OUT} (turns: min {min(lengths)}, max {max(lengths)}, "
          f"avg {sum(lengths) / len(lengths):.1f})")


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:]))
