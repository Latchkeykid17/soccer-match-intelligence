# Submission text (draft)

Copy these into the hackathon "Projects" form. Fill in the two links at the end.

## Project name
MatchLens

## Elevator pitch (short)
MatchLens turns a live feed of synthetic football events into explained, personalised match
intelligence: a stats engine computes every number, seven AI agents on Microsoft Foundry explain
why each moment matters and fact-check themselves, and a live web app renders broadcast-style
overlays for each fan, in their language, for their club and player.

## What it does
- Simulates realistic matches as a live event feed: passes, shots, tackles, pressure, possession
  changes, penalties, aerial duels, cards, substitutions and GPS-style physical data.
- Interprets them live into team and player stats (xG, xGOT, xA, line-breaking passes, sprints, full
  player profiles ranked against everyone who played) and detects patterns: momentum shifts,
  control vs chaos, pressing surges and drops.
- Explains each moment with a team of agents: a Tactical Analyst says why it matters, a Broadcast
  Narrator writes the overlay and commentary, a Fact Checker rejects anything that contradicts the
  data, a Localizer adapts it into 41 languages using local football expressions, and a Recap
  Writer produces half-time and full-time stories.
- Renders it live on a pitch view with timed overlay cards, a momentum chart and team numbers.
- Personalises it: Fan or Analyst mode, language, favourite club, a followed player (highlighted on
  the pitch with a live stat card and an AI spotlight), and a pinned metric.

## Problem it solves
Live football data is everywhere, but numbers alone don't tell fans why a moment matters, and one
broadcast can't suit a casual fan in Lagos and an analyst in London at the same time. MatchLens turns
the same event feed into trustworthy explanations and two genuinely different experiences, in any
market's language.

## How we built it (Microsoft and Azure technology)
- **Microsoft Foundry**: hosts the model deployment (gpt-4.1-mini) used by every agent.
- **Microsoft Agent Framework**: seven specialised agents with structured outputs, coordinated by an
  orchestrator with shared match memory, fact-check feedback loops, retries and safe fallbacks.
- **Azure Container Apps**: hosts the live web app; the API key is stored as a secret.
- **GitHub Codespaces + GitHub Copilot**: development environment and AI pair programmer.
- Python, FastAPI with Server-Sent Events, and a vanilla JavaScript front end.

## What makes it trustworthy
Numbers come from code, never from the model. Every number the AI writes is checked against the
source data, and the Fact Checker reviews every claim. On failure, the overlay falls back to the
engine's factual headline. 20 automated tests cover fault injection. On 40 unseen matches, the
engine detected 79% of the simulator's tactical changes.

## Category
Best Multi-Agent System (also eligible for the Grand Prize).

## Links
- GitHub repository: https://github.com/Latchkeykid17/soccer-match-intelligence
- Live demo: _Azure Container Apps link_
- Demo video: _YouTube link_

## Testing instructions
Open the live demo, choose the AI-explained match and press Start match. Use the panel on the right
to switch between Fan and Analyst, change the language, pick a club and follow a player. The Agents tab
shows each agent working. To run it yourself, see the README.
