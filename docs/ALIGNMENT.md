# Tab ↔ audio alignment: diagnosis and plan

**Status (2026-10-24, 2nd session):** `cdlc build` uses the **hybrid alignment** (`align_hybrid`) with a **tempo DP** in the coarse stage. Benchmark: dev 1.2% of bars off by more than ½ beat (11/12 songs good); validation 6.7% (10/12). **Validated in the game** (MoP and SNA, 2026-10-24). Afterwards, the user noticed that the **end of SNA went out of sync**: the recording has a riff cycle (~4 s) that the tab does not have, and the tempo DP, which chains the bars without gaps, pulled everything after it earlier (it ended at 230.6 s; the music, at 235). Fixed with gaps (point 4 of the method); it now ends at 234.4 s. Deployed `rsTheWhSeven_v8`, pending testing.

**Master of Puppets with the new method** (tab ver 7, 10 s windows against Nacholede): median 10 ms, 0% of windows > 100 ms, 49/49 reliable windows. It fixes the intro (+210 → −30 ms) and the ending (−2,850 → +30 ms) of the previous method. It takes 118 s (60 before). The memory peak, 2.8 GB, is the same as before and comes from the full DTW of `chroma_warp`, which now only provides the global tempo: it could be replaced with something cheap (rerunning the benchmark). (2026-11-18: `chroma_warp` was removed and the peak stays at 2.9 GB, so it did not come from the DTW. The run takes 106 s instead of 119.)
**Seven Nation Army:** matches the validated grid (median 9 ms, maximum 48 ms). The previous hybrid drifted up to 4.7 s in the last 18 bars.
Deployed for testing: `rsMetalMaste_v2_p.psarc` and `rsTheWhSeven_v7_p.psarc`.

## Current method: `align_hybrid`
1. **Starting tempo:** the tab's written tempo. Until 2026-11-18 a harmony DTW (`chroma_warp`) estimated a global tempo ratio first; it was removed because it broke on real tabs (see [Real tabs](#real-tabs-2026-11-18)).
2. **Tempo DP** (`tempo_dp`), the coarse stage. State at each bar boundary = (instant on a 25 ms grid, tempo ratio in 1.5% steps, ±35% around the written tempo). Each bar lasts its nominal length × the ratio. The tempo can change by up to ±3 steps (±4.5%) per bar at almost no cost (0.002 × steps²). A larger change (section change) costs 1.0. Emission = cosine between the tab chroma (16 instants per bar) and the audio CQT chroma, minus its mean over the whole audio. Unsmoothed chroma. The lag of the audio chroma relative to the attack (160 ms) is compensated at the end. Constraint: no note before the audio starts sounding. Empty bars at the ends extend the tempo of the ends.
   - This way it cannot skip cycles of repeated chords: to gain a cycle it would have to speed up unrealistically.
   - The **fine grid is essential**: with 100 ms frames, a 1.6 s bar could only change its duration in steps of ~6%, and the tempo model parameters did nothing.
   - The useful model is the **band limit** plus the section jump, not the quadratic penalty: with more rigidity (0.02–1.0), songs with variable tempo get worse.
3. **Fine step by dynamic programming:** each bar shifts up to ±2.5 beats over the coarse curve. Emission = drum hits from the tab by type (kick → lows, snare → mids, cymbals → highs) plus guitars and bass with low weight. The cost of changing the shift between bars is quadratic. Envelopes compensated by 40 ms for the onset peak lag.
   Also, **fine harmony**: each bar is split into 4 chunks and the tab chroma in each chunk is compared with the audio CQT chroma in the corresponding interval (weight ×3). It resolves passages of isolated chords with rests (F5/E5 at 1:50, 3:10 and 7:40), where drums alone shifted by two beats.
   **Anchor** to the coarse curve: 0.3 × (shift in beats)². Without it, in stretches of ambiguous rhythm it drifted to ±1 beat. Sweep: 0.1 → 3.4%, 0.3 → 1.9%, 1.0 → 3.3%.
4. **Gaps** (audio that is not in the tab). The tempo DP allows, between two bars, a stretch of audio without tab at a cost of 0.2: a "wait" state (accumulated minimum of the cost) with 2 bits per state for backtracking. Two hypotheses are computed, with the proposed gaps and with none, and the **fine step cost**, which sees the attacks, decides. Chroma alone accepted false gaps: in the MoP outro it placed the final chords over the dying resonance. RsBuild receives `bar_ends_ms`: the bar before the gap lasts the normal length, the gap has no notes and beats at the same tempo, and the report warns about it.
   **Tail cut** (the recording ends before the tab, e.g. a fade-out over a written ending): the tempo DP may stop at a bar boundary within 3 s of the audio end, in the second half of the tab, at 0.1 per dropped bar with notes and dropping at least 4 of them. Without it, the end constraint squeezes the whole second half of the song. The fine step and the intermediate stage give the dropped bars no emission, `tail_cut_bar` goes in `bar_times.json`, RsBuild leaves those bars out and the report warns about it.
   Also: no note after the end of the audio, and frames more than 50 dB below the maximum have no chroma (normalized background noise looked like anything).
5. Start constraint in the fine step too, with a **finite penalty**. With `inf`, if no shift satisfied it, all costs were `inf` and `argmin` returned index 0: the whole song was shifted −2.5 beats.
6. **Key check**, around the coarse stage: a tab written in another key than the recording is aligned transposed, and so is a track written in another key than the rest of the tab (see [Tab in another key](#tab-in-another-key-2026-11-18)).

Results of the previous method (DTW + smoothing; 10 s windows against Nacholede): new tab → median 10 ms, 0% of windows > 300 ms. Old tab → median 20 ms, 27% > 300 ms (in the parts where the tab does not match the recording). Seven Nation Army: matches the validated grid (11 ms) except at the end, where the hybrid gets the end of the music right (235 s versus 230 s).

(The first ~5 s of MoP, at +210 ms with this method, are solved by the tempo DP. The old methods `align_beats`, `align_beats_dp`, `refine_local`, `onset_error` and `smooth_warp` were removed from the code; they remain in the git history.)

## Real reference
Master of Puppets CDLC by *Nacholede* (CustomsForge) in `samples/Metallica_Master-of-Puppets_Nacholede_v1_p.psarc`: audio and beat map synced by hand.

To prepare the reference:
- SNG charts → XML: `build/cmp/sng2xml.fsx`.
- Audio: `.wem` → `.ogg` with `ww2ogg` and `revorb`, which are in `external/Rocksmith2014.NET/src/Rocksmith2014.Audio/Tools/win`.

Evaluator in `bench/eval_notes.py`; `bench/mop.py` aligns MoP and evaluates it. For each 10 s window it looks for the shift that makes the most notes **of equal pitch** coincide (±40 ms) between our chart and the reference one. This tolerates parts that the tabs transcribe differently. Windows with less than 30% matches are marked as unreliable.

## Results on Master of Puppets (median |error| per window)
| Method | Error | Windows > 300 ms |
|---|---|---|
| Beat grid + DP (`align_beats_dp`, the one in `cdlc build`) | ~840 ms | 75% |
| + `refine_local` (deployed `_v3` version) | ~820 ms; also adds a bias of ~35 ms | 77% |
| DTW with CENS chroma | ~380 ms | 62% |
| DTW with CENS chroma + snapping to the nearest beat | ~310 ms | 54% |

By beats, the grid is only right between 1:30 and 2:20. In the rest of the song it is off by 0.5 to 3 s.

### With a better tab (`samples/Metallica - Master Of Puppets (ver 7 by cfhbyron666).gp`)
This tab has 11/16 bars (the old one used 5/8 and was missing a sixteenth note in 36 bars), a written tempo of 210/104/212 and 416 bars without repeats.
| Method | Error | Windows > 300 ms |
|---|---|---|
| **Unaligned** (written tempo, starting at the 1st note) | ~390 ms; smooth drift (+900 / −450), **no jumps** | 59% |
| Beat grid + DP | ~1,410 ms | 92% |
| Grid + DP + local tempo | ~1,690 ms | 84% |
| CENS chroma + snapping to the nearest beat | **~260 ms**; many windows at 0–40 ms, the failures are **+1 beat** (~300 ms) | 25% |

Conclusions:
- The new tab is structurally correct: nothing is missing and nothing is extra. The problem is the aligner.
- The beat grid is useless on MoP.
- Chroma gets the bar right, but snapping to the nearest beat sometimes picks the next one. The beat must be chosen with tempo continuity, not by proximity.

## Lessons
- **The onset/drum metric is misleading** with repetitive riffs: a shift of one or more beats scores almost the same. Do not use it to decide. Use real references.
- **beat_this beats** are good in regular stretches, but in the intro, the interlude and the ending of MoP they skip or invent beats (intervals ×0.5 and ×2). Each error shifts everything after it if the grid is followed to the letter.
- **`refine_local` worsens** fine precision, because the peak of the onset envelope comes after the attack. Remove it or correct the bias.
- **Chroma** distinguishes bars with the same rhythm (the chromatic riff E–D#–D–C#), but on its own it is imprecise (~1 beat).
- Comparing beats by index does not work: the references mark beats in other units (5/8 in eighth notes, the interlude at double tempo).

## Plan
1. **Benchmark** with several references. Besides MoP, take 10–20 CDLC from the library (`dlc/`) with a Guitar Pro tab available. If there is no tab, use the reference chart itself as the "tab" with nominal times (beats per bar at a fixed tempo) to test only the alignment.
2. **Hybrid alignment in a single DP over bars.** Emission = bar chroma (tab versus audio, CENS) + onsets. Candidates = positions on the beat grid (half beat). Transition with local tempo continuity and jumps to correct missed or invented beats.
3. Remove `refine_local` or replace it with an unbiased adjustment (compare the start of the attack, not the peak).
4. Make the `cdlc build` report warn when the alignment is doubtful (bars with absurd durations, disagreement between chroma and beats).
5. Validate in the game: Master of Puppets and Seven Nation Army.

## Benchmark (24 official songs/CDLC with a hand-made beat map)
- Selection (`bench/select_v1.py` over `bench/survey.fsx`): 4 groups × 6 songs (metronome or variable tempo × regular or irregular meter), one per artist, split 3/3 between **dev** and **validation**. Validation is not looked at while tuning.
- `bench/extract.fsx` extracts charts and audio; `bench/run.py` builds a fixed-tempo "tab" with the same structure and notes (Lead + Rhythm + Bass, **no drums**), aligns it and compares each bar with notes against the real one.
- Usage: `.venv/Scripts/python bench/run.py --set dev --tag name [--params '{"onset_lag_ms": 20}']`.

### Results (dev, 12 songs)
| Version | Median of medians | Bars off by more than ½ beat | Good songs (<5% of bars wrong) |
|---|---|---|---|
| Hybrid as it was after MoP (counting all bars) | 22 ms | 33.6% | 0/12 |
| + harmony only in the stretch with notes, measuring bars with notes | 17 ms | 25.7% | 4/12 |
| + general start constraint | 17 ms | 25.7% | 4/12 |

### General failures found
1. **Empty bars at the start** (count-in) shifted the harmony DTW → fixed: the DTW only uses the stretch between the first and the last note.
2. **The preference "the first note coincides with the start of the audio" was overfitting to MoP** → replaced by a constraint that is always true (no note before the audio starts sounding).
3. **Repetitive music (identical chord cycles): the DTW skips entire cycles** (Deep Blue Something: −2 bars between bar 11 and bar 31; Machine Head: seconds of error). The fine step (±2.5 beats) cannot correct it. **Pending:** impose a plausible tempo, either by limiting the DTW slope or with a wide search and a second-order tempo model, and measure again with the benchmark.
   **Solved** with the tempo DP (see Current method).
4. **Chroma bias:** the coarse stage placed everything 100 to 300 ms late in every song → unsmoothed chroma and compensated lag.
5. **The fine step drifted to ±1 beat** in stretches of ambiguous rhythm, even when the coarse stage was right (Joy Division) → anchor.
6. **The start constraint with `inf` collapsed the fine DP** (Queensrÿche, −2 beats over the whole song) → finite penalty.
7. **Benchmark bug:** some charts mark 7/8 bars with one beat per eighth note. The benchmark counted them as quarter notes and the bar lasted twice as long → it now counts the ½ units (Queensrÿche and Yes). Long intervals (×2) are left alone: they may be ritardandos.

### Tried and discarded
- **Attacks in the tempo DP emission:** shifts entire songs by several beats (Joy Division −3 s, Machine Head +1.5 s). Without the short radius of the fine step, repetitive riffs fit equally well when shifted.
- **More rigid tempo** (weight 0.02–1.0, or a jump of 3): worsens Machine Head, Mastodon and Lively Ones, which have real tempo variations.

### Results of this session
| Version | Dev: median / >½ beat / good | Validation: median / >½ beat / good |
|---|---|---|
| Starting point (`aacdc94`, DTW + smoothing) | 17 ms / 25.7% / 4 of 12 | 29 ms / 30.0% / 3 of 12 |
| Tempo DP (100 ms frames) + finite start | 11 ms / 5.8% / 7 of 12 | — |
| + 25 ms grid, compensated chroma, anchor 0.3 | 10 ms / 1.9% / 10 of 12 | — |
| + benchmark with bars in eighth notes | **10 ms / 1.2% / 11 of 12** | **14 ms / 6.7% / 10 of 12** |

Validation was run only once, with the final algorithm. The improvement generalizes (30% → 6.7%), but a gap with dev remains, due to two songs.

### Gaps (different structure between tab and recording)
The benchmark did not detect this: its "tab" comes from the chart itself and always has the exact structure. `bench/run.py --drop 2` removes 2 bars from the tab around 60% of the song (the audio keeps them).
| Version | Normal: >½ beat / good | 2 bars dropped: >½ beat / good |
|---|---|---|
| No gaps | 1.2% / 11 of 12 | 20.6% / 1 of 12 |
| Gaps (cost 0.2) decided by chroma | 1.9% / 11 of 12 (2 false) | 8.9% / 8 of 12 |
| **Gaps decided by the fine step** | **1.7% / 11 of 12** (1 false: Mastodon, 18.75 s at b22) | **8.9% / 8 of 12** |

Tried and discarded: raising the gap cost or making it proportional to its duration (SNA and MoP flip at the same time, between 0.3 and 0.5; with 0.05/s it stops detecting the real gaps), and penalizing loud audio after the last note (worsens the normal benchmark to 3.4%, because many recordings keep sounding after the last guitar note).
Validation and MoP: identical, no gaps.
Which of the repeats is the missing one cannot be known in a riff that repeats identically: in SNA the gap ends up before b89.

### Remaining failures (not fixed, to avoid tuning on validation)
- **Mastodon (dev, 6%):** bars 128–150, where the tab has almost no notes (5 of 20 bars, isolated notes). The real tempo is constant and the DP drifts ±5% because nothing holds it.
- **Queen – Love of My Life (validation, 29%):** the coarse stage is right (±0.3 beats) and the fine step goes to +1 beat in slow acoustic stretches (beats of ~780 ms). It is the same failure Joy Division had: the 0.3 anchor is not enough. General idea: make the anchor strength depend on how clear the fine step emission is, or limit the fine step radius in milliseconds as well as in beats.
- **Yes – I've Seen All Good People (validation, 43%):** the coarse stage has an almost constant offset of +1.5 to +2.8 beats over 90 bars of the acoustic part with vocals. The harmony is ambiguous at ±2 beats and the fine step cannot reach.
- Performance: ~20–30 s per song in the tempo DP. Memory for the backtracking pointers: bars × 41 × (duration / 25 ms) bytes, about 350 MB for the 8.5 min of MoP. If it becomes a problem, limit the time window of each bar.


## Session of 2026-11-07: expanded benchmark, anchor in ms and tab intro

### New benchmark
The previous validation set had already been used, so it moves to dev (**24 songs**; the old split stays in `set_v1` of `benchmark.json`, copy in `benchmark_v1.json`). `bench/select_new_val.py` picked **12 new validation songs** (3 per group, no repeated artists): A Perfect Circle, The War on Drugs, Ghost, Elle King, Jim Johnston, Trivium, Spinal Tap, Bach, The Notetrackers (Superman), Third Eye Blind, FUN. and Stereophonics. `bench/select.py` becomes `select_v1.py`: with `bench/` on the path it shadowed the standard `select` module and, when imported, rewrote `benchmark.json`.
New tools: `bench/diag.py` (per-bar error of the coarse stage and the fine step; `--oracle` uses the ground truth as the coarse stage) and `bench/compare.py` (two runs, song by song). The `align_hybrid` result now includes `coarse_bar_times_ms`.

### Changes (both general, now default)
- **Anchor in ms** (`anchor_ref_ms=450`): the fine step anchor measures the shift in units of min(beat, 450 ms) instead of beats. The coarse stage error is in ms (chroma resolution), not in beats: in a slow song (790 ms beat) a one-beat shift is a lot of error and previously cost almost nothing. The minimum ensures the anchor is never weaker than before (with plain 450 ms, Joy Division, which is fast, went from 0.8% to 16.8%).
- **Tab intro** (`lead_slack_bars=2` in `tempo_dp`): the first note cannot sound before the start of the audio plus the duration of the empty bars at the start of the tab, with a margin of 2 bars (added count-in bar). It is a lower bound: a tab that skips the intro is not affected. It removes Mastodon's false gap, which placed the riff over the intro.

### Results
| Version | Dev (24): >½ beat / good | Dev, 2 bars dropped | New validation (12) |
|---|---|---|---|
| Previous (`0b4a9d8`) | 4.2% / 21 of 24 | 8.6% | 5.0% / 9 of 12 |
| + anchor in ms | 3.1% / 22 of 24 | — | — |
| **+ tab intro** | **2.8% / 22 of 24** | **7.0%** | **5.3% / 9 of 12** |

In dev, Queen (29% → 4.6%), Mastodon (13% → 6.3%) and Elvis (2% → 0%) improve, and none gets worse. MoP: identical (10 ms, 0% > 100 ms).
**On the new validation there is no improvement** (5.0% → 5.3%: Bach 1.5% → 4.4%, 2 bars of 73; FUN. 2.7% → 1.8%). Both changes fix failures that existed in dev and do no harm, but the validation failures are of another kind.

Tried and discarded: chroma of the harmonic part (HPSS) in the coarse stage and a more rigid tempo (0.02) for Yes, no effect.

### Remaining failures
- **Yes (dev, 43%):** with the ground truth as the coarse stage, the fine step keeps it (8 of 207 bars wrong) and its cost is clearly better (−454.8 versus −432.0). The failure is in the coarse stage, which drifts up to +2.8 beats over 90 bars of a repeated pattern. The fine step's advantage is small per bar (~0.25 units of `w_ref`) compared with the anchor at 2 beats (~1.2), so it cannot correct it without removing the protection that fixes Queen. Idea: generate several coarse stage hypotheses and choose by fine cost (as with gaps), but a general way to generate them is needed.
- **Spinal Tap (validation, 35%):** after b89 there are ~12 almost empty bars with a real tempo change (beat from 474 to 650 ms); the DP compresses them and everything after comes 16 beats early. Same kind as Mastodon's drift in b129–153.
- **Superman (validation, 16%):** orchestral intro in rubato (beat from 790 to 1,074 ms within a few bars, few notes). The coarse stage starts 19 beats late and chooses a gap (fine cost −335 with the gap versus −331 without it).
- **Common:** stretches with few notes and free tempo. There the tab gives almost no information; the only general thing left is to use more tracks from the tab (the Guitar Pro drums, which the benchmark does not have because it comes from Rocksmith charts).

### Count-in (2026-11-07)
Bars at the start with no pitched notes and only sticks, rim or hi-hat (`COUNT_IN`) are treated as a count-in and their hits are not aligned (`count_in_bars` in the result). In *The Trooper* (Iron Maiden) the tab starts with 4 open hi-hat hits that the recording does not have. The fine step fitted them into the audio and dragged bars 2–3: the first note ended up 350 ms late and those bars were shortened to 1,380 and 1,480 ms versus ~1,540. Now they are 1,580 and 1,530 ms and the first note falls at the start of the audio. From bar 4 onwards nothing changes; MoP and SNA stay identical. The benchmark is not affected because its tabs have no drums.

### Sustained notes and gaps one by one (2026-11-07)
The user tested the Mastodon alignment in the game (*Oblivion*, official charts moved to our timings) and saw that the drift in b129–153 starts with some very long bends. There were two blind spots in the tab:
- **Bends:** the chroma used the unbent note, and the audio sounds up to 2 tones higher.
- **Long notes and ties:** a note only counted in its own bar and for at most 1 s; Guitar Pro tied continuations were discarded. Bars where a note keeps sounding were empty for the alignment.

**Change 1 (`use_held`):** the tab carries a `held` list of notes that sound without a new attack ([t, d, midi]): the bent part of a bend and the continuation of a tied note. They only count for chroma (`_tab_chroma`, `_bar_part_chroma`, `tempo_dp` emission in bars without attacks), not as attacks. It comes from `RsBuild timeline` (Guitar Pro ties and `BendPoints`) and, in the benchmark, from `bendValues` and long notes split per bar.
**Change 2 (`per_gap_select`):** each gap the DP proposes is decided separately. It tries removing it (forbidding ±4 bars) and moving it (forbidding its position and the neighbours), and keeps the option with the best fine cost. Previously all were accepted or none: in Queensrÿche (2 bars dropped) a real gap dragged a false one along, and in Joni Mitchell the gap fell 2 bars early, in a repeated passage, without another position being tried.

| Version | Dev: >½ beat / good | 2 bars dropped: >½ beat / good |
|---|---|---|
| Before (`744ba98`) | 2.8% / 22 of 24 | 7.0% / 17 of 24 |
| **Sustained notes + gaps one by one** | **2.7% / 23 of 24** | **6.5% / 17 of 24** |

In dev only Mastodon changes (6.3% → 3.4%). With 2 bars dropped, Machine Head (22.8% → 15.0%), Mastodon (9.8% → 6.9%) and Queen (7.9% → 6.3%) improve. Queensrÿche (b184 at +0.52 beats) and Norah Jones (b108 at −0.53) each get one bar worse, in zones with 7 and 2 notes; the user accepted the change knowing this. MoP with the real tab (463 tie and bend pieces): the same, 10 ms. SNA: the gap moves from b89 to b96, within the same 2-bar riff that repeats identically, so in the game it lands the same.

Tried and discarded before this, for Mastodon's drift: holding the tempo to the local tempo of the bars with notes (inherits the drift of the first pass; 11 → 28 bars wrong), an alternative curve interpolated over the empty stretches chosen by fine cost (the neighbours are shifted too) and an anchor proportional to note density (the fine step only charges for deviating from the shape of the coarse curve, so it follows its drift). With the true curve as the coarse stage the fine step places all 175 bars of Mastodon correctly: the failure left in Yes and Mastodon is in the coarse stage.
Diagnostic tool used (outside the repo): official charts of a benchmark song moved to the timings of our alignment and packaged, to see it in the game.

### Intermediate stage: correcting coarse stage drifts (2026-11-07)
Yes and Mastodon: with the true curve as the coarse stage, the fine step keeps it and its cost is better; the failure is a slow drift of the coarse stage (chroma prefers it in repeated patterns) that the fine step, being anchored, cannot correct.
**`tempo_refine`:** DP with state (shift at the start of the bar, at the end), up to ±4 beats (max. 2 s) over the coarse stage, 40 ms grid, fine step emission (attacks + per-chunk chroma) and a cost for **changing tempo between consecutive bars**, `refine_weight` × (Δ log ratio)²: a constant tempo costs nothing even if it moves away from the coarse stage. It is only a candidate: it goes through the fine step and is accepted if **fine cost gain + `refine_chroma` × change in the coarse stage chroma > `refine_margin`** (1000, 20 and 1.5). Without the combined criterion, Queen (4.6% → 69%: the fine step, unconstrained, shifts the slow stretches by a beat; the chroma gets much worse) and Joy Division (0.8% → 16.8%: small gain) broke. About 8 s more per song.
Measurements the thresholds come from (weight 1000): Yes +25.0 / −0.74 (113 → 24 bars wrong), Queen +9.1 / −1.45 (4 → 50), Joy Division +3.1 / −0.09 (1 → 25), Mastodon +0.1 / −0.05 (20 → 8, rejected by the margin), OutKast +2.9 / +0.01, Joni Mitchell +2.3 / −0.03. Weights tried: 30 (does not fix Yes), 300 (breaks Cake, Machine Head…), 3000 (worse than 1000).

| Version | Dev (24) | 2 bars dropped | New validation (12, never seen) |
|---|---|---|---|
| Before (`f4b4035`) | 2.7% / 23 good | 6.5% / 17 good | 5.5% / 7 good |
| **Intermediate stage** | **1.0% / 23 good** | **4.9% / 17 good** | **4.1% / 9 good** |

Dev: Yes 43.5% → 6.3%, OutKast and Joni Mitchell → 0%; none gets worse. 2 bars dropped: Yes, Sabaton and Queensrÿche improve; OutKast 3.2% → 3.8% (one bar). New validation (third set, `bench/select_new_val.py --seed 23 --prev validation_v2`: Shinedown, All That Remains, Dream Theater, Audioslave, Audrey and Kate, Manic Street Preachers, Testament, Boston, Grateful Dead, Spacehog, Bill Withers, R.E.M.): Manic Street Preachers 10.4% → 0%, Dream Theater 6.4% → 0%; none gets worse. MoP the same (10 ms). **This validation set is now used.**
Remaining in the validation: Boston (18.9%), Audioslave (17.5%), Audrey and Kate (10.6%); not looked at yet.

### Fixed benchmark: complete tabs (2026-11-07)
Three subagents diagnosed Boston, Audioslave and Audrey and Kate and found a benchmark bug, not an algorithm one. `build_timeline` was losing notes from the charts:
- repeated chords (`<chord chordId=…/>` without `<chordNote>`, between 30 and 90% of the chords in many songs), which are now expanded with their `chordTemplate`;
- entire phrases, because a single difficulty level was used (the one with the most notes); now each phrase uses the level of its `maxDifficulty` (`full_events`).
A real Guitar Pro tab includes all of that, so the benchmark was measuring with tabs much poorer than real ones. Examples: Audioslave goes from 1,496 to 7,149 notes.

| Set (complete tab) | With intermediate stage | Without it (`refine_weight` 0) |
|---|---|---|
| Dev (24) | **2.8% / 22 good** | 3.1% / 21 |
| Dev, 2 bars dropped | **4.6% / 21 good** | 5.0% / 20 |
| Validation v3 (12) | **1.8% / 10 good** | 4.5% / 7 |
| Validation v2 (12) | 2.5% / 10 good | 2.5% / 10 |

With the complete tab, the intermediate stage does not worsen any song in any set (Oasis, OutKast, Joni Mitchell, Sabaton, Shinedown, Audioslave, Audrey and Kate, Manic Street Preachers improve). Compared with the incomplete tab, Mastodon, Machine Head, Silverstein, Steve Winwood, Joy Division and Queen improve, and **Yes (6.3% → 49.8%)** and **Disturbed (0 → 10%)** get worse.
**Yes and Disturbed:** the intermediate stage proposes the good curve (always accepting it: Yes 49.8 → 13.2%, Disturbed 10 → 2%), but the criterion rejects it. With complete tabs, Yes (+6.33 fine gain, −1.15 chroma) and Queen (+7.72, −1.16; accepting it takes it from 3.1 to 53.8%) end up at the same point: no criterion based on those two measures accepts one and rejects the other. The shape of the change does not separate them either (fraction of bars shifted by a whole beat: Queen 53%, Disturbed 46%, Yes 17%, Oasis 12%), so nothing is tuned, to avoid overfitting. With negative gain, always accepting makes things worse (Machine Head, Silverstein, Boston, Spinal Tap). `bench/run.py` now stores `refine_gain` and `refine_chroma_change` per song.
Remaining failures, according to the diagnoses: the Boston intro (an A pedal without attacks and rubato: no information), abrupt tempo changes with fermatas (no stage allows jumps that large; idea: a fixed-cost jump in `tempo_refine`), and short or decaying notes, which are weighted poorly in the chroma (idea: a minimum duration in the tab chroma or attenuating `held`).

### Tail cut: recordings that end before the tab (2026-11-08)
Free Bird (studio version, fades out during the solo; the tab writes the full ending, 256 bars, ~10:19 at its tempos versus 9:14 of audio): the end constraint squeezed bars 108–256 into the audio, ~195 BPM where the band plays at a steady ~150 (librosa, 20 s windows). With the tail cut the coarse stage stops at b236, at the audio end, and the solo stays at 150–155 BPM; suspicious bars 128 → 4.
Tail cost: 0.3 does not cut (only the last 5 bars); 0.1, 0.03 and 0 all cut at b236.
The minimum of 4 dropped bars comes from Audioslave (validation): the chart's last bar is at 350.7 s, past the audio-end threshold (−40 dB, 347.0 s), and without the minimum the DP dropped its last 2 bars (1.7% → 10.7% of bars off).
Results with the final version: dev (24) and validation v3 (12) identical song by song to no tail cut (2.8% / 22 good and 1.8% / 10 good); MoP the same (10 ms, 0% > 100 ms).

### Tab and audio of different songs (2026-11-08)
The build warns when the tab's artist and title share no word with the audio's file name and tags, and when the alignment shows signs of a different song: `chroma_match` (mean chroma emission per bar) < 0.025, median tempo ratio outside 0.85–1.18, or more than 50% of bars off-tempo. Calibration (full alignment, 23 right pairs from the benchmark and `samples/`, 10 crossed pairs): right pairs have `chroma_match` 0.033–0.129, ratio 0.95–1.01 and at most 35% of odd bars (Machine Head); the rule warns on all 10 crossed pairs and on none of the right ones. Three more crossed pairs ran out of memory (long audio).

## Guitar Pro 8 sync points (2026-11-10)

A GP8 tab can carry its own audio track and sync points: a bar (and its repeat pass) mapped to a millisecond of that audio. When a tab has them, the bar times come from the points instead of `align_hybrid` (`align.bar_times_from_sync_points`):
- **Between two points:** time is interpolated in proportion to the written duration (quarters). This is how GP plays them, with one tempo per stretch.
- **Before the first and after the last point:** the nearest stretch's ratio to the written tempo carries on.
- **alphaTab 1.8.4** gives the points already in milliseconds of the audio file, with GP's `FramePadding` already subtracted (`GpifParser`). `RsBuild timeline` writes them as `sync_points` in playback order.

The points refer to the embedded audio, so the pipeline (`--timing auto`) uses them in two cases:
- **No `--audio` given:** the embedded audio is used, and the offset is just the leading silence.
- **`--audio` given:** the points are used only if that audio is the same recording as the tab's (`align.audio_offset`).
  - It cross-correlates onset envelopes on a window at the start of the tab's audio and one at its end.
  - Same recording means Pearson correlation ≥ 0.3 on both windows and a drift between them of ≤ 60 ms.
  - The offset is then refined to the sample by waveform cross-correlation.
  - Anything else falls back to `align_hybrid`.

Checked on alphaTab's `syncpoints-testfile.gp` (12 bars, a repeat, tempo changes, 9 points):

| Check | Result |
|---|---|
| Notes in the built arrangement | Exactly at the points' times |
| Same audio, with 4 s of silence added | Recognized, offset exactly 4000 ms (correlation 0.96) |
| Same audio, 1.5 s trimmed and re-encoded to MP3 | Recognized, offset exactly 2500 ms (correlation 0.94) |
| Same audio, 2 % faster | Rejected: drift −470 ms, correlation 0.12 |
| Another song | Rejected: correlation 0.18 |
| `align_hybrid` on the same audio, against the points | Median difference 7.4 ms, so no offset or padding error |

`align_hybrid` misses two bars by ~1 s. They are the abrupt tempo jumps, exactly where the points help.

Not checked yet: a real song with a GP8 sync, in the game.

## Doubtful bars (2026-11-11)

Without a reference there is no way to know a bar's real error, but there is a way to know whether the audio pins it down. `align_hybrid` measures it per bar after the fine step (`align.bar_confidence`):
- **Lift:** the fine-step emission (onsets per band + chroma per chunk) of the bar where it was placed, minus its best emission shifted by 1 to 2 beats (start and end together), in units of the typical bar weight.
- It is averaged over the bar and 2 neighbors on each side, shifted together. A single bar is too noisy.
- Near or below 0, the audio fits that stretch as well, or better, a beat or more away. The tempo model may have put it there, but nothing in the audio confirms it.

The report and the web app list as **doubtful** the runs of ≥ 5 bars with notes and lift < 0.01 (`align.doubtful_runs`, written to `bar_times.json` as `doubtful_bars`, with the lift of each bar in `bar_confidence`). The measure only reads the result: bar times do not change.

Calibrated with `bench/confidence.py` (collect, then report). A bar is bad if it is off by more than ½ beat. Threshold and run length were picked on dev, then checked on both validation sets:

| Set | Bad bars | Caught (inside a run) | Reported bars that are bad | Runs next to a bad bar | Clean songs with a run |
|---|---|---|---|---|---|
| dev (24) | 136 | 42 % | 84 % | 7/8 | 0/17 |
| validation (12) | 21 | 38 % | 26 % | 1/5 | 0/7 |
| validation_v2 (12) | 43 | 26 % | 65 % | 1/2 | 0/7 |

Bar by bar, the area under the ROC curve (lift against bad bars) is 0.93 on dev and 0.84–0.85 on the validation sets.

What it does not catch:
- **Errors of more than 2 beats** (Superman: up to 12 beats, Boston). The shifted emission does not reach them, and there the emission at the wrong place is often high, so it looks confident.
- **Errors of about ½ beat** (Stereophonics bars 17–31, ~0.5 beat). A half-beat shift is not tested, because on most riffs it scores almost as well as the right place.

False alarms are noisy stretches where the alignment is right anyway (All That Remains bars 17–23 and 103–108, error < 0.1 beat).

The plan's stage B, transcribing the guitar stem to count the matching notes, was not done. It needs Demucs and Basic Pitch.

## Real tabs (2026-11-18)

The benchmark builds its tab from the official chart, so tab and recording always match bar by bar. `bench/real_tabs.py` aligns real Guitar Pro tabs of 12 of those songs and measures them against the same charts.

- **Tabs:** in `samples/bench/<source>/` (not in the repo), matched to a benchmark song by the title in the file name. Two sources:
  - `gprotab`: 15 public tabs of 10 songs, several versions of some.
  - `songsterr`: 13 tabs of the 12 songs. One is a transcription of the Rocksmith chart (Disturbed).
- **Measure:** the tab's notes at their aligned times against the chart's notes (Lead, Rhythm and Bass), in 10 s windows.
  - Each window gets the shift (up to ±3 s) that matches the most chart notes with a tab note of the same pitch within ±40 ms.
  - Among the shifts that reach 90 % of the best count, the nearest to zero is taken: inside a riff that repeats, one cycle away matches as well as in place.
  - A window is **reliable** if at least 30 % of the chart's notes match. Otherwise the tab lacks that part, writes it differently, or is more than 3 s away.
  - A window is **off** if its error is over ½ beat. A tab is **good** with < 5 % of its reliable windows off and ≥ 80 % of its windows reliable.
- **Commands:** `inspect` (bars, written length, key and tempos against the chart), `run` (align and measure) and `report` (measure a saved run again). Each run keeps its files in `build/bench/real/<tag>/`.

What the tabs are like (27 of the 28 load; alphaTab fails on `machine-head-davidian.gp3`):
- **`songsterr`:** all complete and in the recording's key, and all but the Rocksmith transcription have drums.
- **`gprotab`:** 3 cover only part of the song (Yes: 65 s of 368), 2 are in another key (Disturbed, +2 and −1 semitones), one mixes two keys (Dream Theater: the guitar in key, the bass and the keyboards half a step down), and the written tempo is up to 8 % off.

### The harmony DTW broke the tempo search

`chroma_warp` gave `tempo_dp` a global tempo ratio, and `tempo_dp` searched ±35 % around it. On riffs over a single note the DTW collapsed whole stretches of the tab:

| Tab | Ratio from the DTW | Real ratio |
|---|---|---|
| Machine Head (`songsterr`) | 0.61 | 0.99 |
| Machine Head (`gprotab`) | 0.28 | 0.96 |
| Disturbed (`gprotab`, 4 tabs) | 0.05 to 0.67 | 1.00 in the two that are in key |

The real tempo was outside the search range, so the tab ended up squeezed into part of the audio, with up to 5 gaps.

The benchmark could not see it. With its tabs the DTW gave exactly 1.000 in 46 of the 48 songs, and it was wrong in the other two (Machine Head 0.937 for a real 0.992, Third Eye Blind 0.974 for a real 1.000). Its frames are 100 ms, so over a 2 s bar it only tells 1.00 from 0.95 or 1.05.

**Change:** `chroma_warp` is removed and `tempo_dp` searches ±35 % around the tab's written tempo. A written tempo 8 % off is still found (Oasis from `gprotab`: ratio 0.92, 26 of 26 windows right).

| Source (10 s windows) | Before | After |
|---|---|---|
| `songsterr` (13 tabs, 369 windows) | 87 % right, 3.3 % off, 9.8 % unreliable, 9 good tabs | **93.8 % right, 2.2 % off, 4.1 % unreliable, 10 good tabs** |
| `gprotab` (14 tabs, 405 windows) | 48 % right, 7.2 % off, 44.7 % unreliable, 2 good tabs | **63.5 % right, 2.2 % off, 34.3 % unreliable, 4 good tabs** |

- Only 6 tabs change. Both Machine Head tabs and one Disturbed tab become good. Another Disturbed tab goes from 9 to 21 reliable windows of 27, none off. The two transposed Disturbed tabs keep failing.
- The transposed tabs now end with `chroma_match` 0.019 and 0.024, under the 0.025 of the different-song warning. Before, one of them ended at 0.071 with a median tempo ratio of 0.03.
- Benchmark: dev and validation are identical song by song to the previous results (2.8 % / 22 good, 1.8 % / 10 good). So is validation_v2 against the old code run on the same machine (2.5 % / 10 good), except Third Eye Blind: median 10 → 14 ms and bars off by more than 50 ms 33 % → 41 %, none off by more than ½ beat either way.
- MoP is identical window by window (10 ms, 0 % > 100 ms).
- The old DTW crashed with 7 s or more of digital silence inside the audio (NaN in its cost matrix). `align_hybrid` now aligns Seven Nation Army with 10 s of silence written over its middle: outside the silence the bars move by at most 40 ms.

### What the two benchmarks disagree on

The fixed-tempo tab of `bench/run.py` is not always the easier case:
- **Yes:** 49.8 % of bars off with the benchmark tab, no window off with the `songsterr` tab (35 of 38 reliable). The real tab writes the tempo change (130 → 152 BPM) and the benchmark tab has one tempo for the whole song. That is the likely reason; it was not checked.
- **Machine Head:** 0.6 % with the benchmark tab, a total failure with both real tabs before this change.

### Remaining failures with complete tabs (`songsterr`)

- **Audioslave:** the first 5 bars are 1.2 s late (the first chord is at 15.46 s and lands at 16.62 s). The three Audioslave tabs fail the same way.
- **Spinal Tap:** the free-tempo intro. The bar starts are right and the notes inside the bars are up to 0.9 s late.
- **Joy Division:** the last ~10 bars are 1 s early.
- **One window each:** Disturbed (Rocksmith transcription), 2.7 s early at 4:40; All That Remains, 140 ms early at 2:10.

The doubtful-bars warning flags the first three (Audioslave bars 1–5, Spinal Tap 1–6, Joy Division 111–121).

With `gprotab`, most of what is left comes from the tabs: partial ones (Yes, Spinal Tap) and another key (Disturbed, and the bass and keyboards of Dream Theater; see [Tab in another key](#tab-in-another-key-2026-11-18)). Joy Division has long stretches of unreliable windows that were not looked at.

## Tab in another key (2026-11-18)

A tab can be written for another tuning than the recording: E standard for a song recorded half a step down, a capo left out, Drop B for a song in Drop C#. The frets are right and every pitch is off by the same interval, so the chroma of the tab matches nothing in the audio. Two `gprotab` tabs of Disturbed are like that (the recording sounds 2 semitones above one and 1 below the other): neither had a single reliable window.

### The whole tab

Two steps, `align.key_candidates` and then the coarse stage of `align_hybrid`:
1. **Proposal:** the pitch-class profile of the whole tab is correlated with the mean chroma of the audio over the 12 rotations. The rotations that beat no rotation by more than 0.1 are candidates, at most 3. It needs no alignment.
2. **Confirmation:** the coarse stage runs with the tab as written and with each candidate. A candidate wins if its chroma match (mean per bar) is at least 1.2 × the match as written, at least 0.03, and the best.

The rest of the alignment uses the transposed tab. `bar_times.json` gets `key_shift`: the semitones the recording sounds above the tab.

Why two steps:
- **The profile alone is wrong too often.** A fifth away shares most notes, and a tab that covers part of the song has another profile than the whole recording. Of 72 tab and recording pairs in the same key (the 48 benchmark songs and 24 real tabs), 4 correlate best at another rotation and 2 pass the 0.1 margin (the partial Spinal Tap tab at +2, Norah Jones at +5).
- **The coarse stage alone is too slow:** 12 coarse alignments instead of 1. With the proposal, 70 of those 72 pairs run a single one, as before.
- **The proposal rarely misses.** With the profiles of those 72 tabs rotated by ±1, ±2 and ±3 semitones, the right key is among the candidates in 431 of 432 cases.
- **The coarse stage tells the keys apart.** On 27 pairs in the same key, 77 coarse alignments in a wrong key reached at most 0.79 × the match of the right one. The match of those 27 as written was never under 0.035 (median 0.084). The two transposed tabs reach 3.04 × (0.022 → 0.067) and 1.98 × (0.025 → 0.049).

### One track

The Dream Theater tab from `gprotab` mixes two keys: the guitar is in the key of the recording, and the bass and the three keyboard tracks are written half a step down. The whole-tab check followed the majority (+1) and left the guitar a semitone off: 15 reliable windows of 50, against 38 with no key check at all.

`align.track_keys` checks each pitched track on its own after the coarse stage:
- With the coarse bar starts fixed, the track alone is matched to the audio: the chroma emission of the coarse stage, averaged over the bars where the track has notes.
- It is matched as it is, moved by ±1 and ±2 semitones, and moved back to how it was written when the whole tab was transposed.
- The track moves if the best of those is at least 2 × the match as it is, and at least 0.03.
- A track with notes in fewer than 16 bars, or in less than a third of the bars with notes, stays with the tab. So does the only pitched track of a tab.
- If a track moves, the coarse stage runs again with every track in the key of the recording. `bar_times.json` gets `track_key_shifts`: the shift of each pitched track.

Measured on the 26 real tabs with two or more pitched tracks (123 tracks), each track against the audio at the final alignment:
- 88 tracks pass the two size limits. Only one meets the rule: the Dream Theater guitar, 0.010 as aligned and 0.045 as written (4.4 ×). The other tracks of that tab reach at most 0.26 × when moved.
- For the other 87, the best move reaches 0.31 × the match in place (median), 0.50 × (90 % of them) and 0.93 × at most.
- Without the one-third limit there are 109 tracks in the right key, and three sparse ones come close to the rule: 1.98 × (Oasis: 21 notes in 20 of 117 bars), 1.70 × and 1.27 ×. Only the 0.03 floor stopped them (0.025 to 0.029). A wrong move retunes an arrangement and a missed one leaves the tab as it was, so those tracks are left out.
- From the same numbers, a track written 1 or 2 semitones off would be found in 327 of 348 cases (94 %).

Tried and discarded: comparing each track with the rest of the tab, without the audio (cosine of the chroma per frame against the tracks with more notes). A melody or a sparse track in the right key scores like one in the wrong key (ratios up to 2.28).

### Results

| Source (10 s windows) | Before | After |
|---|---|---|
| `songsterr` (13 tabs, 369 windows) | 93.8 % right, 2.2 % off, 4.1 % unreliable, 10 good tabs | the same, tab by tab |
| `gprotab` (14 tabs, 405 windows) | 63.5 % right, 2.2 % off, 34.3 % unreliable, 4 good tabs | **72.3 % right, 3.5 % off, 24.2 % unreliable, 5 good tabs** |

- Three tabs change:
  - **Disturbed** (`.gp4`, +2): 0 → 21 reliable windows of 27, median 5 ms, none off.
  - **Disturbed** (`_8.gp3`, −1): 0 → 12 of 27, 5 of them off. It has a single track and covers part of the song. Those 5 are the new off windows of the table.
  - **Dream Theater** (+1, the guitar stays): 38 → 46 of 50, none off. It becomes a good tab.
- Benchmark: the three sets are identical song by song (dev 2.8 % / 22 good, validation 1.8 % / 10 good, validation_v2 2.5 % / 10 good). MoP is identical window by window.
- The 13 `songsterr` tabs moved up a semitone before aligning: all 13 are found (−1) and every tab gets the same windows as untransposed.
- One track of each `songsterr` tab moved before aligning (the pitched track with the second most notes): found in 11 of 13 moved down a semitone and in 12 of 13 moved up two. Where it is found, the tab gets the same windows as untransposed.
  - **Yes** is missed both times: the track has notes in 23 % of the bars, under the one-third limit. Moved down, it costs one window off.
  - **Joy Division** is missed moved down a semitone: the track matches 1.7 × better where it belongs, under the 2 × of the rule. Its windows stay the same.

### Tuning of the arrangements

A transposed tab still has the right frets, for another tuning. The build (`RsBuild`) adds the shift of each arrangement's track to its tuning and keeps strings and frets, and the report says so:
- **Disturbed** (`.gp4`): written in Drop B, built in Drop C#, the tuning of the official chart.
- **Dream Theater:** the guitar stays in E standard and the bass, written half a step down, is built in E standard. The official Lead and Bass are both in E standard.
- `--keep-tuning` (`keep_tuning` in the pipeline, `--key-shift 0` in `RsBuild`) builds with the tab's tuning. The report then warns that the arrangement will sound off against the recording.

### Not covered

- **Sync points:** a Guitar Pro 8 tab timed by its sync points is not checked. Only `align_hybrid` looks at the key.
- **Recordings between two semitones** (tape speed, a reference other than 440 Hz): the shift is a whole number of semitones.
- **Sparse tracks** (a solo guitar that plays in a fifth of the song) follow the key of the tab.
- **A partial tab in another key** is found but still aligns badly (Disturbed `_8`).
- **The web app** has no switch for `--keep-tuning`.
