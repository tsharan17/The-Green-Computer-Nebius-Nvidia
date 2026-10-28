# Credits and cost

## Green Agent (Token Factory)

Measured from the real prompts and tool results the app builds (system prompt about 2.8k characters, tool definitions about 5.5k, a full `optimise` result about 13k, plus the model's own messages), at roughly 3.5 characters per token:

- A typical agent turn (plan, tool calls, answer) sends about **15,500 input tokens and about 230 output tokens**, because the conversation is re-sent on each of its three model calls.
- At the list prices a third-party catalogue shows (Ultra $1.00 in / $3.00 out per million tokens; Super $0.30 / $0.90; **not verified against Nebius**), that is about **$0.016 per Ultra turn** or **$0.005 per Super turn**. A $25 credit is therefore on the order of 1,500 Ultra turns or 5,000 Super turns. Check the console for real prices.
- The public demo limits each address to 40 agent turns an hour (`GC_AGENT_RATE_PER_HOUR`), so a single visitor cannot drain the credit.

## What spends money, and what the demo switches off

| Action | Cost | In the public demo |
|---|---|---|
| Green Agent turn | Token Factory tokens | Allowed, rate limited |
| Datasheet search | Tavily credit + a Nemotron call | Off |
| GPU benchmark job | Nebius GPU time (billed while it runs) | Off |
| Analysis, report, recipe, measurements | Nothing (runs in the app) | Allowed, rate limited |
| The endpoint itself | VM time while it exists | Choose a CPU platform; delete it after judging |

## Credit rules to respect (Nebius Builder Program terms)

One account per verified email; no sharing, reselling or multiple accounts; prototyping use only; credits expire 90 days after issue (claimed 15 Oct they last to about 13 Jan, past judging on 15 Dec); using more than 30% over your allocation in a month can pause the account.
