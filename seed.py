# seed.py — run once before demo to inject mock events
# Currently commented out — real events come from live API polling.
# Uncomment the events list and run `python seed.py` to restore mock data.

from storage import write_json

# events = [
#
#   # ══════════════════════════════════════════════════════════════
#   # u1 Alice — pattern: calendar → notion → slack (check-in prep)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1 (Alice)
#   {"user_id":"u1","source":"calendar","time":"2024-01-15T09:00:00Z","text":"Weekly check-in with Alice"},
#   {"user_id":"u1","source":"notion",  "time":"2024-01-15T09:04:00Z","text":"Prep notes - Alice"},
#   {"user_id":"u1","source":"slack",   "time":"2024-01-15T09:07:00Z","text":"Messaged Alice about agenda"},
#   # noise — isolated events far from cluster
#   {"user_id":"u1","source":"slack",   "time":"2024-01-15T12:30:00Z","text":"Team lunch reminder sent"},
#   # cluster 2 (Bob)
#   {"user_id":"u1","source":"calendar","time":"2024-01-22T09:00:00Z","text":"Weekly check-in with Bob"},
#   {"user_id":"u1","source":"notion",  "time":"2024-01-22T09:03:00Z","text":"Prep notes - Bob"},
#   {"user_id":"u1","source":"slack",   "time":"2024-01-22T09:06:00Z","text":"Messaged Bob about agenda"},
#   # noise
#   {"user_id":"u1","source":"notion",  "time":"2024-01-22T14:20:00Z","text":"Quarterly review notes"},
#   # cluster 3 (Carol)
#   {"user_id":"u1","source":"calendar","time":"2024-01-29T09:00:00Z","text":"Weekly check-in with Carol"},
#   {"user_id":"u1","source":"notion",  "time":"2024-01-29T09:05:00Z","text":"Prep notes - Carol"},
#   {"user_id":"u1","source":"slack",   "time":"2024-01-29T09:08:00Z","text":"Messaged Carol about agenda"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u2 Bob — pattern: slack → notion (expense tracking)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u2","source":"slack",  "time":"2024-01-15T14:00:00Z","text":"Chased Finance on expenses"},
#   {"user_id":"u2","source":"notion", "time":"2024-01-15T14:08:00Z","text":"Logged expense - Finance"},
#   # noise
#   {"user_id":"u2","source":"calendar","time":"2024-01-16T10:00:00Z","text":"Product demo for stakeholders"},
#   # cluster 2
#   {"user_id":"u2","source":"slack",  "time":"2024-01-22T14:00:00Z","text":"Chased Ops on expenses"},
#   {"user_id":"u2","source":"notion", "time":"2024-01-22T14:06:00Z","text":"Logged expense - Ops"},
#   # noise
#   {"user_id":"u2","source":"slack",  "time":"2024-01-23T11:30:00Z","text":"Vendor contract signed"},
#   # cluster 3
#   {"user_id":"u2","source":"slack",  "time":"2024-01-29T14:00:00Z","text":"Chased Legal on expenses"},
#   {"user_id":"u2","source":"notion", "time":"2024-01-29T14:07:00Z","text":"Logged expense - Legal"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u3 Carol — pattern: notion → slack (task follow-up)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u3","source":"notion", "time":"2024-01-16T10:00:00Z","text":"Reviewed open tasks - Engineering"},
#   {"user_id":"u3","source":"slack",  "time":"2024-01-16T10:05:00Z","text":"Followed up with Engineering on blockers"},
#   # noise
#   {"user_id":"u3","source":"calendar","time":"2024-01-17T14:00:00Z","text":"Design critique session"},
#   # cluster 2
#   {"user_id":"u3","source":"notion", "time":"2024-01-23T10:00:00Z","text":"Reviewed open tasks - Design"},
#   {"user_id":"u3","source":"slack",  "time":"2024-01-23T10:06:00Z","text":"Followed up with Design on blockers"},
#   # noise
#   {"user_id":"u3","source":"notion", "time":"2024-01-24T13:30:00Z","text":"Offsite agenda draft"},
#   # cluster 3
#   {"user_id":"u3","source":"notion", "time":"2024-01-30T10:00:00Z","text":"Reviewed open tasks - Marketing"},
#   {"user_id":"u3","source":"slack",  "time":"2024-01-30T10:04:00Z","text":"Followed up with Marketing on blockers"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u4 Dave — pattern: calendar → slack (meeting reminder)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u4","source":"calendar","time":"2024-01-17T11:00:00Z","text":"Q4 planning meeting in 30 min"},
#   {"user_id":"u4","source":"slack",   "time":"2024-01-17T11:03:00Z","text":"Reminder: Q4 planning starts soon"},
#   # noise
#   {"user_id":"u4","source":"notion",  "time":"2024-01-18T15:20:00Z","text":"Legal sync notes"},
#   # cluster 2
#   {"user_id":"u4","source":"calendar","time":"2024-01-24T11:00:00Z","text":"Budget review meeting in 30 min"},
#   {"user_id":"u4","source":"slack",   "time":"2024-01-24T11:04:00Z","text":"Reminder: Budget review starts soon"},
#   # noise
#   {"user_id":"u4","source":"slack",   "time":"2024-01-25T09:30:00Z","text":"New hire welcome message"},
#   # cluster 3
#   {"user_id":"u4","source":"calendar","time":"2024-01-31T11:00:00Z","text":"Roadmap review meeting in 30 min"},
#   {"user_id":"u4","source":"slack",   "time":"2024-01-31T11:02:00Z","text":"Reminder: Roadmap review starts soon"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u5 Eve — pattern: slack → notion → calendar (standup prep)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u5","source":"slack",   "time":"2024-01-15T07:55:00Z","text":"Pulled updates from Backend team"},
#   {"user_id":"u5","source":"notion",  "time":"2024-01-15T07:58:00Z","text":"Updated standup notes - Backend"},
#   {"user_id":"u5","source":"calendar","time":"2024-01-15T08:00:00Z","text":"Standup - Backend team"},
#   # noise
#   {"user_id":"u5","source":"notion",  "time":"2024-01-15T14:30:00Z","text":"Architecture review notes"},
#   # cluster 2
#   {"user_id":"u5","source":"slack",   "time":"2024-01-22T07:55:00Z","text":"Pulled updates from Frontend team"},
#   {"user_id":"u5","source":"notion",  "time":"2024-01-22T07:57:00Z","text":"Updated standup notes - Frontend"},
#   {"user_id":"u5","source":"calendar","time":"2024-01-22T08:00:00Z","text":"Standup - Frontend team"},
#   # noise
#   {"user_id":"u5","source":"slack",   "time":"2024-01-22T13:30:00Z","text":"Security audit summary"},
#   # cluster 3
#   {"user_id":"u5","source":"slack",   "time":"2024-01-29T07:55:00Z","text":"Pulled updates from Data team"},
#   {"user_id":"u5","source":"notion",  "time":"2024-01-29T07:58:00Z","text":"Updated standup notes - Data"},
#   {"user_id":"u5","source":"calendar","time":"2024-01-29T08:00:00Z","text":"Standup - Data team"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u6 Frank — pattern: notion → calendar → slack (sprint planning)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u6","source":"notion",  "time":"2024-01-15T12:50:00Z","text":"Prepared backlog items for Sprint 12"},
#   {"user_id":"u6","source":"calendar","time":"2024-01-15T13:00:00Z","text":"Sprint planning - Sprint 12"},
#   {"user_id":"u6","source":"slack",   "time":"2024-01-15T13:05:00Z","text":"Sprint 12 goals shared with team"},
#   # noise
#   {"user_id":"u6","source":"calendar","time":"2024-01-16T10:00:00Z","text":"Investor update call"},
#   # cluster 2
#   {"user_id":"u6","source":"notion",  "time":"2024-01-29T12:50:00Z","text":"Prepared backlog items for Sprint 13"},
#   {"user_id":"u6","source":"calendar","time":"2024-01-29T13:00:00Z","text":"Sprint planning - Sprint 13"},
#   {"user_id":"u6","source":"slack",   "time":"2024-01-29T13:04:00Z","text":"Sprint 13 goals shared with team"},
#   # noise
#   {"user_id":"u6","source":"notion",  "time":"2024-01-30T09:30:00Z","text":"Platform migration checklist"},
#   # cluster 3
#   {"user_id":"u6","source":"notion",  "time":"2024-02-12T12:50:00Z","text":"Prepared backlog items for Sprint 14"},
#   {"user_id":"u6","source":"calendar","time":"2024-02-12T13:00:00Z","text":"Sprint planning - Sprint 14"},
#   {"user_id":"u6","source":"slack",   "time":"2024-02-12T13:06:00Z","text":"Sprint 14 goals shared with team"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u7 Grace — pattern: calendar → notion (1:1 meeting notes)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u7","source":"calendar","time":"2024-01-16T15:00:00Z","text":"1:1 with Sarah"},
#   {"user_id":"u7","source":"notion",  "time":"2024-01-16T15:45:00Z","text":"Meeting notes: 1:1 Sarah - actions logged"},
#   # noise
#   {"user_id":"u7","source":"slack",   "time":"2024-01-17T10:30:00Z","text":"Cross-team sync recap"},
#   # cluster 2
#   {"user_id":"u7","source":"calendar","time":"2024-01-23T15:00:00Z","text":"1:1 with Tom"},
#   {"user_id":"u7","source":"notion",  "time":"2024-01-23T15:48:00Z","text":"Meeting notes: 1:1 Tom - actions logged"},
#   # noise
#   {"user_id":"u7","source":"notion",  "time":"2024-01-24T11:30:00Z","text":"Performance calibration notes"},
#   # cluster 3
#   {"user_id":"u7","source":"calendar","time":"2024-01-30T15:00:00Z","text":"1:1 with Priya"},
#   {"user_id":"u7","source":"notion",  "time":"2024-01-30T15:50:00Z","text":"Meeting notes: 1:1 Priya - actions logged"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u8 Hank — pattern: slack → calendar (interview scheduling)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u8","source":"slack",   "time":"2024-01-17T13:55:00Z","text":"Confirmed availability with Frontend candidate"},
#   {"user_id":"u8","source":"calendar","time":"2024-01-17T14:00:00Z","text":"Interview - Frontend candidate"},
#   # noise
#   {"user_id":"u8","source":"notion",  "time":"2024-01-17T17:10:00Z","text":"Hiring debrief notes - Frontend"},
#   # cluster 2
#   {"user_id":"u8","source":"slack",   "time":"2024-01-24T13:57:00Z","text":"Confirmed availability with Backend candidate"},
#   {"user_id":"u8","source":"calendar","time":"2024-01-24T14:00:00Z","text":"Interview - Backend candidate"},
#   # noise
#   {"user_id":"u8","source":"slack",   "time":"2024-01-25T10:15:00Z","text":"Compensation benchmark shared"},
#   # cluster 3
#   {"user_id":"u8","source":"slack",   "time":"2024-01-31T13:56:00Z","text":"Confirmed availability with Data analyst candidate"},
#   {"user_id":"u8","source":"calendar","time":"2024-01-31T14:00:00Z","text":"Interview - Data analyst"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u9 Iris — pattern: notion → slack → calendar (project update)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u9","source":"notion",  "time":"2024-01-18T08:50:00Z","text":"Drafted project status - Alpha launch"},
#   {"user_id":"u9","source":"slack",   "time":"2024-01-18T08:54:00Z","text":"Posted Alpha launch update to #general"},
#   {"user_id":"u9","source":"calendar","time":"2024-01-18T09:00:00Z","text":"Project update - Alpha launch"},
#   # noise
#   {"user_id":"u9","source":"notion",  "time":"2024-01-19T13:30:00Z","text":"Bug bash tracking doc"},
#   # cluster 2
#   {"user_id":"u9","source":"notion",  "time":"2024-01-25T08:51:00Z","text":"Drafted project status - Beta launch"},
#   {"user_id":"u9","source":"slack",   "time":"2024-01-25T08:55:00Z","text":"Posted Beta launch update to #general"},
#   {"user_id":"u9","source":"calendar","time":"2024-01-25T09:00:00Z","text":"Project update - Beta launch"},
#   # noise
#   {"user_id":"u9","source":"slack",   "time":"2024-01-26T10:20:00Z","text":"Compliance training completed"},
#   # cluster 3
#   {"user_id":"u9","source":"notion",  "time":"2024-02-01T08:52:00Z","text":"Drafted project status - GA release"},
#   {"user_id":"u9","source":"slack",   "time":"2024-02-01T08:56:00Z","text":"Posted GA release update to #general"},
#   {"user_id":"u9","source":"calendar","time":"2024-02-01T09:00:00Z","text":"Project update - GA release"},
#
#   # ══════════════════════════════════════════════════════════════
#   # u10 Jake — pattern: calendar → slack → notion (client onboarding)
#   # ══════════════════════════════════════════════════════════════
#   # cluster 1
#   {"user_id":"u10","source":"calendar","time":"2024-01-16T10:00:00Z","text":"Client onboarding - Acme Corp"},
#   {"user_id":"u10","source":"slack",   "time":"2024-01-16T10:03:00Z","text":"Welcome message sent to Acme Corp"},
#   {"user_id":"u10","source":"notion",  "time":"2024-01-16T10:07:00Z","text":"Created onboarding doc for Acme Corp"},
#   # noise
#   {"user_id":"u10","source":"notion",  "time":"2024-01-17T14:30:00Z","text":"Internal tooling review notes"},
#   # cluster 2
#   {"user_id":"u10","source":"calendar","time":"2024-01-23T10:00:00Z","text":"Client onboarding - Globex Corp"},
#   {"user_id":"u10","source":"slack",   "time":"2024-01-23T10:02:00Z","text":"Welcome message sent to Globex Corp"},
#   {"user_id":"u10","source":"notion",  "time":"2024-01-23T10:06:00Z","text":"Created onboarding doc for Globex Corp"},
#   # noise
#   {"user_id":"u10","source":"slack",   "time":"2024-01-24T15:20:00Z","text":"Waystar renewal negotiation recap"},
#   # cluster 3
#   {"user_id":"u10","source":"calendar","time":"2024-01-30T10:00:00Z","text":"Client onboarding - Initech"},
#   {"user_id":"u10","source":"slack",   "time":"2024-01-30T10:04:00Z","text":"Welcome message sent to Initech"},
#   {"user_id":"u10","source":"notion",  "time":"2024-01-30T10:08:00Z","text":"Created onboarding doc for Initech"},
#
# ]
#
# write_json("event_log.json", events)
# print(f"Seeded {len(events)} events across 10 users.")

print("seed.py is commented out — real events come from live API polling.")
print("To restore mock data: uncomment the events list and re-run.")
