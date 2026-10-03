How do you build AI agents that compete on Kaggle and keep improving with no human in the loop? Podium's design 🧵

𝗧𝗵𝗲 𝗹𝗼𝗼𝗽
A Google ADK LoopAgent runs every 60 s: Scout → Gatekeeper → Solver → Critic → Submitter. One experiment thread per competition, so a long run never blocks the fleet.

𝟭. 𝗦𝗰𝗼𝘂𝘁: pick battles you can win
A 0–100 "rank chance" score from category, time left, data size, metric, daily quota, team count and the full public leaderboard. Boards topped by leaked perfect scores are penalised.

𝟮. 𝗦𝘁𝗿𝗮𝘁𝗲𝗴𝗶𝘀𝘁: think before coding
• Data profile in a sandbox, including adversarial validation (train vs test AUC) and time-split detection.
• Inputs: a playbook per archetype, the highest-scoring public notebooks, web research on past winning solutions.
• Output: a plan with an expert team, a validation design, a hypothesis queue and score targets ("≤ 0.3903 for top 10%").
• It re-plans every 4 experiments, after each new leaderboard score, and after 6 runs without a new best.

𝟯. 𝗦𝗼𝗹𝘃𝗲𝗿: search, don't guess
• One persistent LLM conversation per competition: every round feeds back the CV, diagnostics, review notes and leaderboard.
• Tree search: UCB picks which experiment to extend; every 6th run explores a new branch; every 4th blends the top out-of-fold (OOF) predictions; a 5-seed rebuild before the deadline.
• Validation first: when CV and the leaderboard disagree, fixing the split comes before any new feature. CV is only compared within the same validation scheme.
• A shared workspace holds fold indices, a feature cache and Optuna studies that persist across experiments.

𝟰. 𝗖𝗿𝗶𝘁𝗶𝗰: trust nothing
Deterministic checks (ids, ranges, NaN, format), then an LLM code review that fails on leakage, such as early stopping on the scored fold. A failed review means no submission, and the reason goes back to the Solver.

𝟱. 𝗦𝘂𝗯𝗺𝗶𝘁𝘁𝗲𝗿: spend quota like money
It submits only when the CV gain beats the fold std, probes the leaderboard with near-ties, and reads the real rank. Final picks are the best CV plus the most robust candidate (CV − std).

𝗟𝗲𝗮𝗿𝗻𝗶𝗻𝗴 𝗮𝗰𝗿𝗼𝘀𝘀 𝗰𝗼𝗺𝗽𝗲𝘁𝗶𝘁𝗶𝗼𝗻𝘀
Each reflection distils transferable lessons into a fleet memory that every new plan starts from.

𝗘𝘅𝗲𝗰𝘂𝘁𝗶𝗼𝗻
Docker sandbox with no network and no secrets, with CPU/RAM fair share. GPU and code competitions run as private Kaggle notebooks.

𝗠𝗼𝗱𝗲𝗹𝘀
One model per role, any provider. Mine run on my Claude Code subscription: Fable 5.1 plans, Opus 5.5 executes and reviews. Podium reads the real plan usage and keeps a reserve for me.

𝗦𝘁𝗮𝗰𝗸
Python, FastAPI, SQLite, SSE, Docker, Kaggle API; a dashboard and a Copilot that acts on the system.

𝗥𝗲𝘀𝘂𝗹𝘁 𝘀𝗼 𝗳𝗮𝗿
On Store Sales, the agents found their own lag leakage and went from #395 to #76 (top 14%) in a day. No medal yet.

What would you add: true MCTS, RL fine-tuning, or something else?

#AIAgents #Kaggle #MachineLearning #MLOps #LLM
