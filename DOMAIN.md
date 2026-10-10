# Shipping primer

Everything you need to know about the domain for this task. When this document and an email
disagree on a contract term, the email (the contract) wins.

## The contract

The broker's **recap** lists the agreed terms. Treat it as the complete contract for laytime and
demurrage. An **addendum** sent later amends the recap: the amended term replaces the original
for the whole voyage.

## The players

| Who | Role |
|---|---|
| **Owner / operator** (Halcyon Tankers, us) | Runs the vessel. Receives demurrage when the charterer keeps the vessel too long. |
| **Charterer** | Rents the vessel for one voyage to move their cargo. Pays demurrage. |
| **Broker** | Negotiates the deal and sends the **recap**: the agreed contract terms. |
| **Agent** | Local company that handles the vessel in a given port and reports what happens there. |
| **Master** | The captain. Sends position reports and arrival / departure messages from the vessel. |
| **Terminal** | The facility on shore where cargo is loaded or discharged. |

## A voyage

A **voyage** is one trip under one contract (the **charter party**). Its **itinerary** is a list of
**port calls**. Each port call has a function:

- **Load**: cargo goes on board.
- **Discharge**: cargo comes off.
- **Other** (bunkering, i.e. refuelling; canal transit; waiting): no cargo is moved.

Only **load and discharge** port calls count for laytime. The voyage ends when the vessel sails
from its last discharge port; where it goes next belongs to the next voyage.

## Events in a port call

In roughly the order they happen:

| Event | Meaning |
|---|---|
| **ETA / ETD** | Estimated time of arrival / departure. A forecast; it is revised often. |
| **EOSP** | End of sea passage: the vessel has actually arrived off the port. |
| **Anchored** | The vessel waits at the anchorage, usually because the berth is busy. |
| **NOR tendered** | Notice of readiness: the master formally declares the vessel ready to load or discharge. This is the key event that starts the laytime clock. |
| **Anchor aweigh** | The vessel lifts anchor to move towards the berth. |
| **POB** | Pilot on board. |
| **All fast** | The vessel is moored at the berth. |
| **Hoses connected** | Cargo hoses connected between vessel and shore. |
| **Commenced / completed loading or discharge** | Cargo operations start / finish. |
| **Hoses disconnected** | Cargo hoses removed. This is the key event that stops the laytime clock. |
| **Sailed** | The vessel leaves the port. |

The agent sends a **statement of facts (SOF)**: the official, time-stamped log of these events at
a port, including any stoppages and their reason. Agents sometimes send an **interim SOF** while
operations are still running, followed by a final one.

## Times

- SOFs and agents' notices give times in **local time (LT)** of the port.
- Masters often write times in **UTC** (also written GMT or "Z"). Check the label.
- Email `Date:` headers show when the email was sent, in the sender's timezone. They say nothing
  about when an event happened.
- Common shorthand: `15/1630` means the 15th of the current month at 16:30. `PM` without a time
  means "sometime in the afternoon".

## Abbreviations

| Term | Meaning |
|---|---|
| AGW WP | "All going well, weather permitting": a caveat on an estimate |
| B/L | Bill of lading: the official cargo document and quantity |
| BDN | Bunker delivery note: receipt for fuel taken on board |
| COSP | Commencement of sea passage: the vessel has left the port |
| DA | Disbursement account: the agent's invoice for port costs |
| Despatch | Money paid by the owner if the charterer finishes early ("NIL" = none) |
| ETC | Estimated time of completion (of cargo operations) |
| Laycan | The date window in which the vessel must arrive at the load port |
| LT | Local time |
| OPL | Outside port limits |
| PDPR | Per day pro rata |
| ROB | Remaining on board (fuel quantities) |
| SHINC | Sundays and holidays included: the laytime clock runs every day |
| WAT / CET | West Africa Time / Central European Time, both UTC+1 in winter |

## Laytime and demurrage

The charterer gets a fixed amount of time, the **laytime allowance**, to load and discharge. If
they take longer, they pay the owner **demurrage**, a daily rate for the extra time.

**At each load or discharge port:**

1. Laytime **starts** when the NOR was tendered **plus the turn time** (a fixed number of hours
   set in the contract). That is the only start rule: it applies whether the vessel is still
   waiting at anchorage or already at the berth.
2. Laytime **ends** when the hoses are disconnected after cargo operations are complete.
3. **Used laytime** at the port = (end − start) − deductions.

**Deductions** are periods inside that window that the contract says do not count, or count only
partly. Typical examples: time spent moving from the anchorage to the berth, or time lost because
of a problem on the vessel. Delays caused by the terminal or by waiting for a berth normally
**do** count. The contract recap says which deductions apply and at what percentage (a stoppage
that "counts as half laytime" means half of its duration is deducted).

**For the voyage:**

- If laytime is **reversible**, the used time of all load and discharge ports is added up and
  compared with one shared allowance.
- Once the allowance is used up, the vessel is **on demurrage**. Whether deductions still apply
  after that point depends on the contract wording.
- **Time on demurrage** = total used − allowance, if positive.
- **Demurrage owed** = time on demurrage (in days, including fractions of a day) × the demurrage
  rate per day. "Pro rata" (PDPR) means partial days are charged proportionally.

**Example.** Allowance 48 hours, reversible. Rate USD 20,000 per day pro rata. Turn time 6 hours.

| Port | NOR tendered | Laytime start | Hoses disconnected | Gross | Deductions | Used |
|---|---|---|---|---|---|---|
| Load | 01 Mar 08:00 | 01 Mar 14:00 | 02 Mar 20:00 | 30 h | 2 h (vessel pump failure) | 28 h |
| Discharge | 10 Mar 22:00 | 11 Mar 04:00 | 12 Mar 10:00 | 30 h | 0 h | 30 h |

Used 58 h against 48 h allowed, so 10 h on demurrage: 10 / 24 × 20,000 = **USD 8,333.33**.

The answer is **final** once hoses are disconnected at every load and discharge port, and
**provisional** before that.
