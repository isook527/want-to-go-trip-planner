# Want to Go evaluations

`evals.json` lists synthetic traveler requests, fixture requirements and observable pass criteria. To run a prompt evaluation, give the agent the prompt and the described fixture in a clean temporary directory. Supply simulated source results for blocked sites, hours and flights. Do not make real bookings or use personal travel material. Judge against the expected output after the run.

The named deterministic regressions run with `python -m unittest discover -s tests`. These cover tool behavior. Skill routing, wording and choices such as declining a booking request need a prompt evaluation with the host agent.
