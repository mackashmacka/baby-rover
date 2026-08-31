# Career track

The second deliverable. It runs **in parallel** with the build from Day 0 — not
as a week-three scramble.

---

## 1. The principle

**Evidence is harvested daily; the artifacts are assembled at the end.**

By week three the memory of *why* a decision was made is gone, and a report
written from a cold repo reads like a report written from a cold repo. The
CLOSE ritual already forces a daily memory entry and an experiment registry row.
That discipline is what makes the report writeable in two days instead of two
weeks.

**The hard rule: he writes the prose.** Claude Code assembles the raw material —
pulls the registry, the plots, the git log, the memory pages — and critiques
drafts hard. It does not ghostwrite. A report he did not write is one he cannot
defend when an interviewer asks a follow-up question, and defending it is the
entire point of having it.

---

## 2. What is being harvested, daily

| Artifact | Where | When |
|---|---|---|
| Decisions, dead ends, reasoning | `memory/YYYY-MM-DD-*.md` | every session |
| Experiment rows + data | `experiments/REGISTRY.md` + CSVs | every experiment |
| Plots | `experiments/plots/` | as generated |
| Photos of wiring, benches, failures | `docs/media/` | as they happen |
| **Video** | `docs/media/` | every time something works |

**Film the failures too.** A rover driving into a box because the ground-plane
fit was wrong is a better story than one that never failed, and interviewers
know it.

---

## 3. The report

His. Written by him, in week three, from material that already exists.

The structure falls straight out of the work — which is the point of doing the
work in this order:

1. **The problem** — a rover that crosses a room and avoids what's in the way
2. **Hardware** — the split by timing determinism, and why
3. **Characterisation** — four "identical" motors, quantified as different
4. **Control** — PID, and why open loop was never going to hold
5. **Sensing** — what each cheap sensor actually gives you, error and all
6. **Fusion** — the ladder, one plot, four tracks, each better than the last
7. **Navigation** — the two courses
8. **What broke** — the wrong-wires evening, the 6F22, the analyser driver
9. **What I'd do differently**

**Section 8 is not an appendix.** Walking the causal chain, and splitting the
system in half, are the two techniques that actually found the bugs — and the
ability to describe a debugging method is the single most interview-legible
skill in the whole project.

---

## 4. LinkedIn

**One strong post beats five thin ones.** Post it when the indoor course works.

- Lead with the 20-second video of the rover avoiding a box
- Two plots: the four-motor characterisation overlay, and the fusion ladder
- Three or four sentences on **one** specific thing he learned — the
  magnetometer/motor interference, or why wheel odometry lies in a skid-steer
  turn. Specific and technical beats broad and enthusiastic, every time
- Link the public GitHub repo

Then keep posting through the build, briefly. A build log reads as genuine in a
way a single polished announcement does not.

---

## 5. CV

He already has more than he thinks. The job is mapping it, not inventing it.

| What he has | What it demonstrates |
|---|---|
| Managed a fast-food restaurant | Operations under pressure, staff coordination, real accountability. Employers read this as *reliable* — most graduates have nothing like it |
| Strong marks, good university (UNSW) | Technical baseline |
| Society involvement | Initiative outside coursework |
| **This project** | Embedded C, sensor fusion, control theory, systems debugging, and a public repo anyone can check |

**Lead the technical section with the project, and quantify it.** Not "built a
robot" — "characterised four gearmotors and closed a PID loop at 100 Hz",
"reduced closed-course position error from X m to Y m across four fusion
stages." The numbers exist because Week 1 and 2 were spent producing them.

Do not bury the restaurant job. Technical graduates who have genuinely managed
people are rare.

---

## 6. Outreach

**Step one is deciding what job he actually wants** — the outreach is shaped
completely differently for a robotics startup than for a graduate programme.
This is his decision, and it should be made in week two, not week three.

### Targets worth researching (Australia / Sydney)

- **INS/GNSS and navigation** — the most on-point category by a distance, given
  the project is literally an EKF over GNSS + IMU + odometry. Advanced
  Navigation is Sydney-based and works in exactly this space
- **Defence and space** — a growing sector with real embedded demand
- **Mining and agricultural automation** — the largest employer of autonomy
  engineers in Australia, and consistently under-applied-to by graduates
- **Drone and robotics startups** — small teams, will actually read a repo
- **UNSW academics** — his own current and past lecturers first. Research
  assistant and summer-scholarship positions are real, frequently unadvertised,
  and asked for far less often than they should be
- **The UNSW careers service** — underused; they have employer relationships he
  cannot get to alone

### How to do it without it backfiring

**Personalised, researched, and sent by him.** Claude Code researches the
company, finds the specific team, and drafts — he reads every one before it
goes.

**Do not mass-mail HR departments.** A hundred generic emails is worse than ten
good ones: it converts badly, and in a sector this small in Australia, being
remembered as the person who spam-blasted is an actual cost.

The opening line should be about **them**, and the second should be a link to
something he built that is relevant to what they do. For a navigation company,
that is the fusion-ladder plot. That is why the plot exists.

---

## 7. Suggested timing

| When | Do |
|---|---|
| Day 0 | GitHub public, repo pushed |
| Daily | Memory entry, registry row, photos |
| End of week 1 | Characterisation plots done — first LinkedIn build-log post |
| Week 2 | **Decide the target job type.** Draft the CV. Email professors — do this early, academics are slow |
| Week 3, days 1–3 | Indoor run works → video → the main LinkedIn post |
| Week 3, days 4–6 | Write the report. Finalise the CV |
| Week 3, day 7 | Send the outreach. Ten good ones |
| After | Keep the repo alive. A project with commits after the "finished" post reads very differently |
