# Demo video script (1 minute 55 seconds)

Judges stop watching at 2:00, so keep it under. Record your screen with the Windows Game Bar
(Win + Alt + R) or OBS, and speak over it. **No music**: the rules forbid copyrighted material.

**Before recording**
- In the codespace, from matchsim, run `uvicorn app.server:app --port 8000`, open the Ports tab and click the globe next to 8000, so the AI features work live. Full screen.
- Pick the match marked "(AI explained)". Set the speed to 120×.
- Set "Watching as" to Fan, Language to English, My club None, Follow player None.
- Have the GitHub README open in a second tab, scrolled to the architecture diagram.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:12 | The app, before pressing Start | "Football stats tell you *what* happened, not *why*. And one broadcast can't fit a casual fan in Lagos and an analyst in London. This is MatchLens." |
| 0:12–0:35 | Press **Start match**. Let players move and the first overlay land. Click the **Moments** tab | "A synthetic match streams in live. A stats engine computes every number exactly, and seven AI agents built with Microsoft Agent Framework on Microsoft Foundry explain why each moment matters, right on the broadcast." |
| 0:35–0:50 | Click the **Agents** tab while a moment arrives | "Here's the team at work: a Tactical Analyst, a Narrator, and a separate Fact Checker. If a single number doesn't match the data, the draft is rejected and rewritten. Numbers come from code, never from the AI." |
| 0:50–1:05 | Switch **Fan → Analyst**. Open the **Match** tab (momentum chart, team numbers) | "Same match, two genuinely different experiences. Fans get plain language; analysts get xG, pressing rates and momentum." |
| 1:05–1:20 | Change **Language** to Français, then Arabic | "Every moment is adapted into the viewer's language, using local football expressions only when they fit." |
| 1:20–1:38 | Pick **My club**, then **Follow player**: the gold ring appears and the Player tab opens. Click **Ask the AI** | "Pick your club and the story is told from your side. Follow a player and you get their live stat card, ranked against everyone on the pitch, plus an AI spotlight." |
| 1:38–1:46 | **Match** tab → scroll to **Download the data** | "Analysts can download every stat as a spreadsheet, or the full event feed for a data pipeline." |
| 1:46–1:55 | Switch to the README diagram tab | "Microsoft Foundry and Agent Framework, built in GitHub Codespaces with GitHub Copilot. MatchLens: every match, explained for every fan." |

**Upload:** YouTube → Create → Upload video. Title: "MatchLens: explainable live match intelligence".
Visibility: **Public** or **Unlisted** (not Private), or judges can't watch it. Copy the link.

**Tips**
- Do one practice run first. If the match moves too fast, use 60×.
- If you stumble, keep going; you can trim the start and end in the YouTube editor.
- Speak a little slower than feels natural.
