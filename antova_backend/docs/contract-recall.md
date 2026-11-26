# Contract-term recall

How well the model locator finds the contract terms (CT-01 to CT-17), the bar it must clear, and what
happens to a term that does not clear it.

## The live run

The synthetic call-off pack `samples/TENDER-2026-419` holds every contract term across two offers, two
framework agreements (one amended) and an owner-contract excerpt. Its answer key (`expected.json`,
`ideal_contract_locate` and `contract`) is written by hand.

Score the configured model against it, from saved answers (free):

```
python -m antova_ingest.locator_eval --pack TENDER-2026-419 --contract
```

Record answers for documents that have none first (this calls the paid API):

```
python -m antova_ingest.locator_eval --pack TENDER-2026-419 --contract --live --spend
```

The pack is synthetic (`"synthetic": true`), so its contract documents pass the zero-retention guard
without `ANTOVA_ZERO_RETENTION_REF`. The report has a "Contract terms" table with each term's matched,
expected and recall.

## The bar

Each contract term's recall on pack 419 must be at least the equipment ontology's per-parameter recall
on pack 402, as measured with the same model:

```
python -m antova_ingest.locator_eval --pack TENDER-2026-402
```

| Measured | Model | Pack 402 equipment recall (the bar) |
|---|---|---|
| not yet measured | not yet measured | fill in after the first live run |

No measured figure is recorded in the repository yet. After the first live run, fill in this table with
the date, the model, and the lowest per-parameter recall on pack 402. Then list here any contract terms
on pack 419 that fall below it.

## Terms below the bar: manual

A term below the bar is marked manual in `antova_ingest/contract_ontology.py` by starting its
`locate_hint` with `"MANUAL:"` (`MANUAL_PREFIX`). The contracted stage then never takes that term from
the documents. For every vendor its state is `missing`, it carries the flag `manual_term`, and the
reason is "not reliably found; check by hand" (`MANUAL_REASON`). The approval sheet shows that reason
for each of its fields, and the contract cell stays open until a reviewer has checked it.

Remove the prefix once a later live run shows the term at or above the bar.
