1. Data is stored in JSON/JSONL; no database is required for this version.

2. The controlled event vocabulary is limited to the events described in
   DOMAIN.md. Unsupported events are retained as "other" and flagged for review.

3. Laytime for a cargo port closes when final hoses are disconnected.

4. A port call closes when the vessel sails, but its laytime result remains part
   of the current voyage's cumulative calculation.

5. A voyage closes when the vessel sails from its final discharge port.

6. Events after the final-discharge sailing do not change the completed voyage's
   laytime. They belong to the next voyage, or remain unassigned until that
   voyage is identified.

7. A late email may still correct or document a completed port call or voyage.
   Association uses the event/document context, not the email sent date.

8. For this version, all recorded emails and extracted facts associated with a
   voyage can be loaded together when building its answer. The pipeline does not
   need granular retrieval by port, event type, or time window.

9. More selective retrieval and indexed storage are scalability concerns for a
   future version. They are not required for the supplied 19-email dataset.

10. Inbound mail is not parsed inside this pipeline. A separate ingestion layer
    (mailbox sync, export, or drop folder) is assumed to land each message as a
    `.eml` file under `emails/`. The demurrage pipeline only reads from that
    directory and from the append-only `extractions.jsonl` it maintains.

11. After the initial batch run, new mail is handled by a **live** path (not a
    full re-import of the whole folder on every arrival). The intended production
    shape is a worker that watches `emails/` for changes (new or updated files).
    When a change is detected, the worker triggers the live pipeline, which:

    - parses and extracts only the new email(s);
    - appends each result to the existing batch-recorded `extractions.jsonl`;
    - rebuilds the affected voyage answer in Python from **all** extractions
      associated with that voyage, plus `fleet.json`;
    - writes updated `result.json` and `brief.md`.

    Batch is the way to bootstrap or rebuild from scratch: it clears
    `extractions.jsonl` and runs the same live step on every email in `emails/`.

12. The directory watcher / worker that polls or subscribes to `emails/` is
    **out of scope** for this repository. It is assumed to call the live entry
    point when files change, for example `demurrage live` or
    `demurrage live path/to/new.eml`. Both modes live in
    `src/demurrage/pipeline.py`.