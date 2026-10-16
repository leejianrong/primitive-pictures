# Prompt files

Plain text, one prompt per line, as `orchestrate.py --prompts FILE` reads them
(blank lines ignored; there is no comment syntax, so notes live here).

## video-scenes.txt

Eight scenes for judging the frame-sequence mode (`primitive -frames`, ADR-0009)
on more than one clip. The same line drives both the still (FLUX) and the
Wan2.2 image-to-video motion, so each describes motion as well as the scene.
Each stresses carry-forward differently:

1. sailboat on a lake: the control, the scene the first tuning was done on
2. fox trotting through snow, camera following: small mover on a static background
3. night market, people walking: detail and motion everywhere, the hard case
4. waves on rocks at golden hour: fast foam texture over static rock
5. hiker on a ridge above clouds: slow camera, large soft shapes
6. coffee with steam by a rainy window: near-static, small local change
7. flamingos, a few taking off: mixed motion, clean palette
8. vintage car, tracking shot: whole-frame pan, where carried shapes go stale fastest

Run: `RP_GPU_TYPE=... uv run python orchestrate.py --video --max-hourly-usd 2.6
--prompts prompts/video-scenes.txt` (see the pod-image/GPU notes in CLAUDE.md).
