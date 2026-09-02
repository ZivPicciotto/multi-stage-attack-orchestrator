# Multi-Stage Attack Orchestrator

Hi — this is my submission for the Multi-Stage Attack Orchestrator take-home exercise.

**One important note up front:** this is a made-up scenario for the purpose of the exercise. There
is no real device anywhere in this project, and nothing here performs or describes a real security
exploit. Every "attack" and "stage" name you'll see is just a made-up label I used to give the
simulation something concrete to work with — think of it the same way you'd think of a placeholder
like "Task A" or "Step 2." The interesting part of this exercise isn't the made-up attacks — it's
how the software is put together: how it makes decisions, handles things going wrong, and talks to
another program over a network.

## Where everything is

```
Attack_Orchestrator_Exercise.docx   the original brief I was given
SharedProtocol/                     the shared "message format" both programs agree on (see below)
MultiAttackOrchestrator/            Part 1 -- the Python decision-making program, and its tests
Simulator/                          Part 2 -- the C program that plays the role of "the device"
```

Both `MultiAttackOrchestrator/` and `Simulator/` have a `plans/` folder inside them. Before writing
any code for a given piece, I wrote a short document explaining what I was about to build and why —
those documents go into more depth than this README does, if you want to see the reasoning behind a
specific piece.

One honest caveat about those documents: I wrote each one *before* writing the code it describes,
and the code sometimes changed afterward — a rename, a bug caught during implementation, a detail
that turned out differently once it was actually built. I didn't go back and keep every one of
those documents word-for-word in sync with every later change, so a few of them describe an earlier
version of a design rather than the exact final code. The reasoning in them is still accurate; a
specific detail here or there (a file name, a function signature) occasionally isn't. If something
in a planning document looks inconsistent with the actual code, trust the code — that's the same
default I'd want a reader to apply to anyone's design docs.

## How to actually run it

If you want to see it working rather than just reading about it:

```bash
cd MultiAttackOrchestrator
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'    # needs Python 3.11 or newer

.venv/bin/pytest -q                                            # runs all the automated checks
.venv/bin/python -m orchestrator.demo                          # runs a set of example scenarios
```

The last command runs several example scenarios back to back and prints out, step by step, what
the program is doing and why — a method being tried, a step failing and being retried, something
going wrong and the whole attempt restarting, and so on. It's the fastest way to get a feel for how
the thing behaves.

To also build and try the C program:

```bash
cd Simulator && make                                     # builds the practice "device"
.venv/bin/python -m orchestrator.demo --tcp               # same example scenarios, but talking
                                                            # to the real C program this time
```

## Part 1 — the decision-making program

### Everything talks to "the device" through one single doorway

The most important decision I made in this whole project was to have every part of the
decision-making code talk to "the device" through exactly one narrow interface — a small,
well-defined set of things you can ask a device to do (tell me about yourself, attempt this step,
list your files, give me this file). Nothing else in the program is allowed to know or care whether
the thing on the other end of that interface is my pretend stand-in device or the real C program
from Part 2.

The payoff of that decision is exactly what you'd hope for: I built and fully tested all the
decision-making logic against the pretend device first, and then when the real C program was ready,
plugging it in required changing **nothing** in the decision-making code — only one small new file
that knows how to speak over the network. I verified this directly: I ran the same set of example
scenarios against the pretend device and against the real program, and the results — what was
attempted, what succeeded or failed, in what order — came back identical both times.

### A method's "chance of success" is just an estimate — it isn't what actually happens

Every method the program can try comes with a number representing how likely I estimate it is to
work. That number is only ever used to decide **which order to try things in** — it is never used
to decide whether something actually works. Whether a step actually works is entirely up to the
device it's talking to (real or pretend) — the program asks the device to attempt the step, and the
device's answer is the only thing that matters.

I think this is a realistic way to model it: a real tool would also pick what it *believes* is the
most reliable approach based on past experience, but reality still has the final say once it's
actually running against a real device in front of it.

### How the program decides which method to try first

For a method made up of several steps, I multiply together the estimated success chance of each
step to get one overall score for that method, and the program tries the most promising method
first, falling back to the next one if it doesn't work.

I want to be upfront that this is a simplified way of ranking things. A more complete version of
this tool would also weigh other things — for example, how much useful data a method can actually
recover, or how costly it is if an attempt fails. I didn't think building a fully realistic scoring
system was the point of this exercise, so instead I added one narrower safety knob: each method has
a limit on how many times it's allowed to restart from scratch after something goes wrong.
Cheaper, safer methods are allowed to restart several times; a method where a failed attempt is
more costly is set to not restart at all. It's a partial answer to a bigger question, and I'd rather
say that plainly than pretend the simple scoring covers everything.

**One thing I want to flag directly:** the brief describes deciding on one method, running it, and
extracting once it succeeds — it doesn't explicitly say what should happen if that one method fails
outright. Automatically falling through and trying the next-ranked method is something I chose to
add, not something the brief asked for in so many words. I think it's a reasonable extension of
"several may be valid for the same device — how do you pick?" rather than unnecessary complexity,
since a real tool would obviously want to try something else rather than just give up. But I'd
rather say plainly that this is my own addition than have it look like I assumed it was required.

### Three different ways a step can go wrong, handled three different ways

| What happened | What the program does about it |
|---|---|
| The step just didn't work this time, but the device is fine | Try that same step again, up to a limited number of times, before giving up on this method. |
| The step didn't work **and** it broke the device's current session | Reconnect and start this method over completely from its first step. |
| The connection to the device was lost entirely (dropped, or timed out) | Same as above: reconnect and start the method over from the beginning. |

The first two are treated as *normal answers* the program gets back — "it worked," "it didn't,"
"it didn't and something broke." The third is treated differently, as an actual error, because
it means something more fundamental: the program didn't get an answer *at all*. I kept those two
situations separate on purpose. "The device told me it broke" and "I have no idea what happened to
the device" call for the same recovery action here, but they're genuinely different situations, and
collapsing them into one thing would have thrown away information that's useful when you're trying
to understand what a run actually did.

### Steps that need to remember something from an earlier step

Most steps in a method are self-contained. But I also wanted to demonstrate a case where a later
step genuinely needs a piece of information an earlier step produced — for example, a step that
can't even attempt anything until an earlier step has handed it something to work with. I built a
small shared notebook that's passed along through every step of one attempt: a step can write a
result into it, and a later step can read from it. If that later step's information is missing —
because the earlier step never produced it — the step refuses to even contact the device and
reports a normal, ordinary failure instead. Nothing else about the program needed to change to
support this; a "missing information" failure is handled exactly the same way any other failure is.

### Getting data off the device once something works

Once a method succeeds, a separate part of the program copies data off the device. You can ask for
just confirmation that it's unlocked, one specific file, a specific list of files, or everything on
the device. The result is honest about partial success — if you asked for five files and only three
came back (one didn't exist, say, or the connection dropped partway through), the program tells you
exactly that, rather than reporting one single pass/fail answer for the whole request.

### Making impossible situations actually impossible

While reviewing my own code, I found a couple of places where the data types I'd chosen could
technically represent a situation that doesn't make sense — for example, a step result that claimed
to have both "succeeded" and "broken the device" at the same time, which should never happen
together. I went back and changed those types so that the nonsensical combination genuinely cannot
be constructed anymore, rather than just being "a case I promise never happens." I'd rather the
program's own data types rule it out than rely on remembering not to do it.

## Part 2 — the practice device

This is a second, separate program, written in C, that stands in for a real device. It runs on its
own and listens for a connection over the network, the same way a real device would — and Part 1
talks to it exactly the same way it would talk to a real one, because of the "one single doorway"
decision described above.

**Proof it actually behaves the same as the pretend stand-in:** I ran the exact same set of example
scenarios against both — the in-memory pretend device, and the real, separate C program — and
diffed the results. Every single one came back identical: same method chosen, same order of
attempts, same successes and failures. That, to me, is the real proof that the "one doorway"
decision from Part 1 paid off the way it was supposed to.

### Both programs need to agree on exactly how they talk to each other

Since Part 1 (Python) and Part 2 (C) are two completely separate programs talking over a network,
they both need to agree — down to the exact byte — on the format of the messages they send back and
forth. Rather than writing that format twice by hand (and risking the two copies quietly drifting
apart over time), I wrote it once, in a plain data file, and used a small script to automatically
generate the matching code for both sides from that one file. If I ever need to change the message
format, I change it in one place and regenerate both sides — there's no way for them to end up out
of sync. There's an automated check that catches it immediately if someone edits the generated code
by hand instead of regenerating it.

The messages themselves are simple: the Python side can ask the device four things — tell me about
yourself, attempt this step, list your files, or give me this file. The device can answer with: it
worked, it didn't work (but the device is fine), it broke, the file wasn't found, or something about
the message didn't make sense. One detail I was careful about: when a step "breaks" the device, the
device still sends back one complete, proper answer explaining what happened — and only *after*
that does it go silent. That mirrors exactly how the pretend stand-in device behaves, which is why
Part 1's code doesn't need to know or care which one it's actually talking to.

### The practice device reads its setup from a file — nothing is hardcoded

Rather than baking specific method or step names into the C program itself, it reads everything it
needs — what the pretend device looks like, what files it has, how each step should behave — from a
plain text file when it starts up. That means I can add a new method on the Python side without
ever touching the C program again; I just describe the new scenario in a text file. There's a folder
of example scenario files, one per example in the demo I mentioned above.

### A couple of smaller, practical decisions

- I used a small, well-known, freely licensed library to read those text-file setups, rather than
  writing my own code to parse that format — that felt like effort better spent on the parts of the
  exercise that actually mattered.
- The C program only ever deals with one connection at a time. It doesn't try to handle several at
  once, because nothing about this exercise actually needed that, and handling several at once
  would have added real complexity for no real benefit here.
- C doesn't automatically stop you from reading more data into a fixed-size space than it can hold,
  the way Python does — so throughout the C program, every piece of incoming data is checked
  against the space available for it before it's copied anywhere. This isn't something I had to
  think about at all on the Python side; it's one of the genuine differences between the two
  languages that this exercise was clearly designed to make me confront.
- I also added logging throughout the C program, so if you run it yourself you can watch it narrate
  each connection and each request it receives, the same way the Python side already narrates what
  it's doing.

## Part 3 — testing the whole thing together

I wrote automated tests that actually launch the real C program and check that Part 1 behaves
correctly against it — not just against the pretend stand-in. These tests deliberately reuse the
same set of checks that were already written against the pretend device, just pointed at the real
program instead. All of them pass. The fact that the same checks pass against both the pretend
device and the real one is itself further evidence that the two behave the same way.

One area I'd still like to expand, if I had more time, is testing the C program with more
deliberately unusual or malformed input, and testing what happens if several connections show up
at once (even though, as noted above, the program is intentionally built to handle one at a time).
Neither of those feels required to call this exercise finished, but they're the honest next step.

## A few decisions I want to explain directly

The exercise notes that I should call out places where I saw more than one reasonable option and
explain what I chose and why. Here's a summary of the main ones, most of which are also explained
in more depth above:

- **Falling through to the next-ranked method on failure, which the brief doesn't explicitly ask
  for.** The brief describes deciding on one method and running it; it's silent on what happens if
  that one fails outright. I chose to keep trying lower-ranked methods rather than stop at the
  first failure, since that's what a real tool would do — but I want to be upfront that this is an
  addition on my part, not a literal requirement I was following.
- **A simple scoring system for ranking methods, instead of a more complete one.** I explained the
  reasoning above — I didn't think a fully realistic multi-factor scoring system was the point of
  this exercise, so I built the simpler version and was explicit about what it leaves out, rather
  than pretending it covers everything.
- **Treating "no answer at all" differently from "an answer that says it didn't work."** Also
  explained above — I think collapsing those two into one thing would have thrown away useful
  information, even though the program's recovery step happens to be the same either way.
- **A general "rulebook" for talking to a device, rather than requiring one specific base class.**
  This is what let the real, separate C program and the in-memory pretend device both count as
  valid devices, without being related to each other in the code at all — which suits two things
  that live on opposite sides of a network connection and are written in different languages.
- **The practice device reads its setup from a file, instead of having methods built into its own
  code.** I considered just hardcoding the example methods directly into the C program, but that
  would mean every new method added on the Python side also needs a matching change to the C
  program — which defeats the point of having a generic stand-in device at all.
- **One shared message-format file that generates matching code for both languages**, instead of
  writing that format by hand twice. This is the one place where the two programs genuinely have to
  agree byte-for-byte, so I didn't want to trust that to two hand-maintained copies staying in sync.
- **The practice device handles one connection at a time, deliberately kept simple.** Since this
  exercise needed a predictable, easy-to-reason-about stand-in rather than a production server, I
  didn't see a reason to take on the real complexity that handling multiple connections at once
  would have required.
