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