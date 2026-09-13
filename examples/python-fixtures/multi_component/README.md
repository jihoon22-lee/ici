# multi_component

A root with two Python components under `components/`. The defect lives in
`beta` only; `alpha` is its in-fixture control, so a finding attributed to
`alpha` is a misattribution rather than a second detection.

Today's ici has no component concept — `discover_project_model` produces one
flat model — so it analyses this as a single project. That is the point: the
fixture records what the current implementation does, and the same input
becomes the "after" measurement once components land (#207).
