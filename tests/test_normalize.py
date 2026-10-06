from app.pipeline.normalize import from_messages, from_transcript


def test_transcript_with_timestamps_and_continuation():
    turns = from_transcript("[00:01] Agent: Hello there\nCustomer: my bill\nis wrong\nREP: sorry")
    assert [(t.turn_id, t.speaker, t.text) for t in turns] == [
        (1, "agent", "Hello there"), (2, "customer", "my bill is wrong"), (3, "agent", "sorry")]


def test_chat_messages_merge_bursts_within_window():
    msgs = [
        {"sender": "user", "message": "hi", "timestamp": "2026-10-01T10:00:00Z"},
        {"sender": "user", "message": "my bill 😡", "timestamp": "2026-10-01T10:00:20Z"},
        {"sender": "agent", "message": "Hello!", "timestamp": "2026-10-01T10:01:00Z"},
        {"sender": "user", "message": "still there?", "timestamp": "2026-10-01T10:30:00Z"},
        {"sender": "user", "message": "hello??", "timestamp": "2026-10-01T11:30:00Z"},
    ]
    turns = from_messages(msgs)
    assert [t.text for t in turns] == ["hi my bill 😡", "Hello!", "still there?", "hello??"]


def test_corpus_rows_map_client_to_customer():
    turns = from_messages([{"speaker": "client", "text": "x", "date_time": "2023-09-09T15:08:03+00:00"}])
    assert turns[0].speaker == "customer"
