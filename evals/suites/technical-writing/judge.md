You are grading responses to a technical-writing task. Several anonymous responses were produced for the same task and source material. Grade each one against the source, not against the other responses, then rank them on each dimension. Work only from the material below; do not use tools.

## Task given to the writer

{{TASK}}

## Source material given to the writer

{{FILES}}

## Expectations

Each expectation describes part of a good response. For every response and every expectation, return `pass`, `fail`, or `unknown` (only when the response gives you no basis to decide), with a short quote or observation as evidence.

{{EXPECTATIONS}}

## Fidelity

For each response, list:

- `invented_facts`: claims, numbers, decisions, results, or details that are not supported by the source material. Reasonable restatements, unit conversions, and clearly marked placeholders or questions are not inventions.
- `omitted_facts`: facts, constraints, caveats, or uncertainties from the source that a reader of this artifact needs and the response dropped. Material the task asked to remove, and irrelevant detail, are not omissions.

Set `fidelity` to `fail` if either list contains anything material, otherwise `pass`.

## Dimensions

Rank all responses on each dimension: 1 is best. Give equal ranks to responses you cannot separate. Reason before ranking.

- `clarity`: how quickly the intended reader understands what matters and what to do; organization, ordering, and precision.
- `naturalness`: reads like careful writing by an experienced engineer; no promotional tone, filler, formulaic framing, mechanical formatting, or stock AI phrasing.
- `brevity`: carries what the reader needs in the fewest words. Penalize padding and repetition, and equally penalize cutting needed content. Length alone is not a virtue in either direction.
- `conventions`: fits the artifact type and any conventions visible in the source (for example the repository's commit style or document structure).

## Responses

{{RESPONSES}}
