# For judges: how to try The Green Computer in two minutes

**Track:** Best Apps and Agents. **Started:** 13 October 2026, inside the submission period (see the git history).
**Demo:** https://green-computer-7yy6xwxdna-ue.a.run.app (free, no payment, stays up until the judging period ends).

## Getting in

Create a free account on the demo page (email and a password; no email confirmation is needed). Every new account comes with an
example project, **Example: YOLOv8s on a robot (30 FPS, 55 W, 40 °C)**. [If you set up a ready test account, put it here: email
and password.]

## The two-minute path

The app is one numbered path, with a stepper at the top and Back / Next buttons on every page.

1. Open the example project. **Step 1, Inputs** is already filled in: a YOLOv8s object detector on a Jetson Orin NX, 30 inputs a second, at most 55 W, in a 40 °C room. Press **Analyse**.
2. **Step 3, Analysis** shows today's setup only: it **fails**, because the chip overheats and slows itself to about 70% of its speed. You see what limits it, the numbers against your limits, a board heat map, the warm-up curve, where the energy goes and power over a batch. Nothing is recommended yet.
3. **Step 4, Recommendations: Try changes.** Click **Start with the top-ranked change** (or any change in the list). The engine re-runs the analysis with that change: the new verdict, what moves (for example FP16 cuts energy per input by about half and raises throughput several times), and the same figures side by side, today and with the change. Software and hardware changes are listed separately; chip-design ideas are marked directional.
4. **Step 5, Validation.** *Build and compare a plan* combines changes and compares each alone and all together against today; the recommended plan meets every requirement and uses about 59% less power. *Saved plans* keeps each version unchanged.
5. **Step 6, Export** downloads a report, the plan, and a recipe (scripts to convert the model, build the engine, set the Jetson power mode and measure on the board).

For other cases, **Start a new analysis** offers example cards (a smart camera, ResNet-50 on a Jetson AGX Orin, a Raspberry Pi camera with no fan, keyword spotting on a microcontroller, person detection on an FPGA), and **Add five example projects** on the Workspace page fills your workspace with analysed ones. When a setup cannot meet a requirement, the app says exactly why and never loosens your requirement to make it pass.

## How it uses NVIDIA Nemotron and Nebius

The engine computes every number. The language model only explains and suggests, and every reply is checked.

- **Green Agent** (sidebar, **Ask the assistant**): **NVIDIA Nemotron on Nebius Token Factory** plans and answers by calling the engine as tools. A guardrail rejects any figure in its reply that is not in a tool result, and it cannot change your requirements. Allowed in the demo, rate limited to protect credits.
- **AI candidates** (Recommendations, **Every recommendation, in full**, bottom of the page): Nemotron proposes extra changes as JSON. They are schema-checked, unknown change ids lose their link, unsupported figures are removed, and each is labelled *not evaluated* until you run it through the plan builder.
- **Datasheet import** (**Tavily** finds datasheet pages for hardware that is not in the library; Nemotron Nano extracts each value with its source page; a person confirms them) is **on in the public demo**, limited to 5 imports an hour per visitor. Try it under Inputs > Hardware: "Your hardware isn't in the list?", for example "Hailo-8L". It takes 45 to 60 s. **Nebius Serverless Jobs on NVIDIA GPUs** (real benchmarks that feed calibration) are switched **off in the public demo** because they spend credits. They are in the code and the README; see below for what has been run.

Where Token Factory is called: `backend/app/agent/` (client, agent loop, tools, recommender, datasheet). Model ids default to Nemotron Ultra, Super and Nano and are matched against the provider's own model list at run time.

## Please read: what is and is not verified

- The performance, power and heat numbers are **predictions** from a physics model with stated assumptions and best-case to worst-case ranges. The only real measurements so far are ResNet-50 and MobileNetV2 latency on the developer's CPU. **No Jetson, GPU or power measurement exists yet**, so Jetson results are analytical.
- **Green Agent: verified live** on 27 October 2026, on `nvidia/Nemotron-3-Ultra-550b-a55b`. The agent answered in the app, and a scripted check confirmed a full tool-call round trip.
- **AI candidates: verified live** on 27 October 2026, on `nvidia/nemotron-3-super-120b-a12b`. The first two attempts failed: the reply was not valid JSON, then a reasoning model used its whole token budget thinking and returned no answer. The app now repairs the common JSON slip, retries once, and allows more room; after that, 4 of 4 runs returned six candidates each, in 12 to 21 seconds.
- **Datasheet import (Tavily + Nemotron Nano): verified live** on a local copy, 27 October 2026. Four real devices were drafted, each value tied to its source page, and one went through confirmation and analysis. It is on in the public demo, rate limited.
- **Nebius Serverless Jobs and Endpoints: not run live.** They are tested only against fakes.
- The app has automated tests: a backend suite and 15 browser journeys with accessibility checks, all passing locally on 27 October 2026.
- Generated recipes are syntax-checked, never run on a device. The chip-design what-ifs come from a simple model with no on-chip memory detail and are directional.

## Run your own copy

See the [README](../README.md): `scripts/dev.ps1` (Windows) or `scripts/dev.sh` builds the interface and serves http://localhost:8000. Put `TOKEN_FACTORY_API_KEY` in `.env` to switch the agent on. `scripts/acceptance.py <url>` checks a running copy end to end as a judge would.
