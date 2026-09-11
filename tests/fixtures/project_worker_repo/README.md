# Project Worker Fixture

Small deterministic repository with one intentional code defect. Tests expect
`calculator.add(2, 3)` to return `5`; the initial implementation subtracts.
It is copied to a temporary directory by the end-to-end worker fixture test.
