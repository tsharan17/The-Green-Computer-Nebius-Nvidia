# Demo video script (target 2:40, hard limit 3:00)

Record the real app in a clean browser window at desktop width. No music, or royalty-free only; no NVIDIA or Nebius logos (names in text are fine). Do not show keys or `.env`. Upload to YouTube as **public**.

The video must show the project working. Show the Green Agent only if it answers live when you record; **do not show anything as working that has not been run** (see "Before recording").

| Time | On screen | Say (roughly) |
|---|---|---|
| 0:00 | Workspace page | "Will your AI model run on your hardware, and how do you make it greener? The Green Computer predicts that before you build anything, then shows exactly what to change, and what the analysis looks like after each change." |
| 0:12 | Open the example project; the numbered stepper at the top; Step 1 inputs | "One path, six steps. Here a robot has to run an object detector at 30 frames a second, under 55 watts, in a 40 degree room." |
| 0:25 | Press **Analyse** | "One click. The engine solves performance, power, heat and throttling together." |
| 0:35 | **Step 3, Analysis**: verdict, what limits it, the four figures (board heat map first) | "Step three is only about today. It fails: the chip overheats and slows itself to seventy percent of its speed. Here is the heat on the board, the warm-up, where the energy goes. No recommendation yet." |
| 0:58 | **Thermal and cooling** (More detail): the *warmest room each cooling option can handle* chart | "For cooling: how warm a room each option can handle. Our current heatsink fails in this room, and so does a bigger passive one; a heatsink with a fan has room to spare." |
| 1:12 | **Step 4, Try changes**: click the top-ranked change | "Step four is the recommendation engine. Click any change and the whole analysis re-runs with it." |
| 1:25 | The updated analysis: verdict before and after, what moves, and the figures side by side | "New verdict, what moved, and the same figures, today next to the change. Requirements are never loosened." |
| 1:42 | Click a hardware change (a better cooler), then a chip idea marked directional | "Software, hardware, and chip-design ideas, which we label directional because they are what-ifs." |
| 1:52 | **Ask the assistant** (Green Agent): one real question, e.g. "Why does it fail today?" *(only if it answers live)* | "The Green Agent, powered by NVIDIA Nemotron on Nebius Token Factory, explains by calling the engine as tools. A guardrail rejects any number that did not come from the engine." |
| 2:12 | **Step 5**: build the plan, the comparison, save | "Combine changes into a plan, compare each alone and together against today, and save it as an immutable version." |
| 2:25 | **Step 6, Export** | "Finally a shareable report and a recipe: scripts to convert the model, build the engine and measure on the board." |
| 2:35 | Workspace page | "Predict. Optimise. Verify. Make every computation count." |

Optional, only if each works live and fits in the time: **AI candidates** (Recommendations, *Every recommendation, in full*, "Ask the AI for candidates"), and **datasheet import with Tavily** (Start a new analysis, describe your own hardware). Datasheet search and GPU jobs are switched off in the public demo, so record these from your own local copy, and say so.

## Before recording

- Run `scripts/acceptance.py <url>` against the demo.
- Run `scripts/check_nebius.py` again on the day (it passed on 27 October). AI candidates passed 4 of 4 live runs on 27 October; if they fail on the day, leave them out of the video.
- Datasheet import with Tavily worked live on 27 October (45 to 60 s per device). To show it for the Tavily bonus, record it from your local copy and cut the wait; a device that is not in the library works best (for example "NVIDIA Jetson Orin Nano Super Developer Kit").
- Delete earlier test projects so the workspace is tidy; use a fresh account.
- Keep it under three minutes: judges need not watch beyond that.
