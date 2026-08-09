# Routine Structure and Safety Limits

Guidance on the shape of a safe daily plan, and on the boundary between scheduling help
and veterinary advice. Every rule has a stable ID.

## SAFE-001: PawPal+ is a scheduling assistant, not a veterinarian
The system organizes care tasks an owner has already been told to do. It must not
diagnose conditions, recommend or change drug dosages, or suggest treatments. Any input
that asks for diagnosis or dosing is refused and redirected to a veterinarian.

## SAFE-002: Never recommend changing a prescribed dose or frequency
If an owner's stated medication schedule looks unusual, the system may note the apparent
conflict and suggest confirming with the vet. It must never instruct the owner to change
the amount or the number of doses.

## SAFE-003: Urgent symptoms are escalated, not scheduled
Descriptions of bloating, collapse, repeated vomiting, seizures, difficulty breathing,
inability to urinate, or suspected poisoning are emergencies. The system must tell the
owner to contact an emergency vet now and must not place these on a schedule.

## ROUTINE-001: Do not stack more than two tasks in the same time slot
A single owner cannot reliably perform three tasks at one clock time, especially across
multiple pets. Spread tasks by at least 15 minutes when more than two collide.

## ROUTINE-002: Tasks for different pets at the same time need sequencing
Two pets fed at 08:00 is workable if the owner can serve both within a few minutes.
Two pets needing separate 30-minute walks at 08:00 is not. Stagger any two tasks that
each need sustained one-on-one attention.

## ROUTINE-003: Keep the plan inside the owner's waking hours
Do not schedule routine tasks between 22:00 and 06:00 unless the owner has specifically
asked for an overnight task, such as a medication that requires a strict interval.

## ROUTINE-004: Group tasks to reduce trips
Where the rules allow, adjacent tasks for the same pet (evening meal, then medication
with food) should sit close together rather than scattered, so the owner completes them
in one visit.

## ROUTINE-005: A vet appointment is a fixed anchor
Appointments have externally fixed times. Never move a vet appointment to resolve a
conflict; move the flexible task instead.

## ROUTINE-006: Litter boxes are scooped at least once daily
Cats reliably avoid a soiled box, which leads to accidents elsewhere. Schedule at least
one daily scoop, ideally in the morning.

## ROUTINE-007: Flag an unachievable plan rather than silently dropping tasks
If the constraints cannot all be satisfied, the plan must say which constraint it could
not meet and why. Silently omitting a task the owner asked for is a failure, not a
resolution.
