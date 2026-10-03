# LinkedIn post: Podium

**Attach:** `podium-demo-linkedin.mp4` (60 s, captions burned in, email and username masked). Use `cover.png` as the thumbnail if LinkedIn lets you choose one.

---

My AI agents found a bug in their own work, fixed it, and climbed 319 places on a Kaggle leaderboard. I wasn't involved.

For the past few weeks I've been building **Podium**: a team of AI agents that enters real Kaggle competitions on its own and tries to climb the leaderboard.

It's not a notebook and it's not AutoML. It's a full loop:

🔎 A **Scout** finds live competitions and scores its own chance of ranking well. It reads the public leaderboard to spot competitions where the top is just leaked perfect scores.
🧠 A **Strategist** profiles the data, reads the best public notebooks, researches past winning solutions, and writes a plan. Every few experiments it rewrites that plan from the evidence.
🧪 A **Solver** writes and runs each experiment in a locked-down sandbox. It works as one long conversation per competition, so it builds on everything it has learned.
🛡️ A **Critic** reviews every script before submission: leakage, validation, format. If the review fails, nothing is submitted.
📈 A **Submitter** stays within Kaggle's daily limits, reads the real leaderboard and tracks the rank.

My favourite moment was on Store Sales, a time-series forecasting competition. We were stuck at **#395 out of 538**. The Strategist noticed our validation score and the leaderboard disagreed by 0.13. It traced that to a leak: the features used 1–15-day lags that don't exist across the 16-day forecast window. The agents rebuilt the validation themselves. Same day: **#76 (top 14%)**, with no human in the loop.

It also keeps a "fleet memory": general lessons it carries into every new competition. One from day one: *"A validation change is a refactor, not an experiment: assert the submission is identical before and after."*

What surprised me most:
• **The bottleneck wasn't the model, it was honesty about the validation.** Most of the gains came from making the local score match the leaderboard.
• **Agents need a cockpit.** I built a live dashboard and a Copilot I can chat with. It knows the whole system and can act on it ("what would get us into the top 10%?" gets a specific, sourced plan).
• **All of it runs on my Claude Code subscription.** Opus 5.5 does the work and Fable 5.1 does the strategy. Podium watches my real plan usage and leaves a reserve for me.

It's not winning medals yet: top 14% on one competition, top 45% on another. Next it's entering Google DeepMind's **Gemma 4 Developer Agent** challenge, where the goal flips: building a small, local coding agent that teaches itself.

Building in public. If you work on autonomous agents, MLOps or Kaggle, I'd love your take: **where would you trust an agent like this, and where wouldn't you?**

#AIAgents #Kaggle #MachineLearning #ClaudeCode #MLOps #BuildInPublic

---

## Notes before posting

- **Check the numbers on the day you post** (Overview and Ongoing pages). Today: Store Sales #76/538 (top 14%), Playground S6E10 #249/554 (top 45%).
- **Don't claim** "state of the art" or "best in the world" until a benchmark or a medal backs it up.
- **The Gemma line is accurate as written** ("entering": v1 is submitted). Update it once you have a score.
- **Optional first comment:** a link to a repo or write-up, if you publish one.
- **Video:** `podium-demo-linkedin.mp4` is 60 s at 1.5× speed; `podium-demo-full.mp4` is the 90 s real-time version.
