freeCodeCamp records when a signed-in camper completes a Daily Coding Challenge, but the platform does not yet track engagement momentum. We want to add a deterministic streak feature so campers can see how many consecutive days they have completed a Daily Coding Challenge.

Goal: introduce a canonical streak model derived from the set of UTC calendar days on which a user has completed at least one Daily Coding Challenge, then persist and expose it.

Streak definition (must be pure and deterministic):
- A "completion day" is the UTC calendar day (YYYY-MM-DD in UTC) of a Daily Coding Challenge completion timestamp.
- currentStreak counts consecutive completion days ending on either the current UTC day or the previous UTC day (a camper does not lose the streak until they miss a full day). If the most recent completion day is older than yesterday, currentStreak is 0.
- longestStreak is the maximum run of consecutive completion days ever observed.
- lastCompletionDay is the ISO date string of the most recent completion day, or null if none.
- Duplicate completions on the same UTC day never increase either streak.

Behavior requirements:
- When a Daily Coding Challenge completion is submitted through the protected challenge route, the user's persisted streak fields must be recomputed from the full completion history and returned in the completion response payload alongside existing fields.
- The Daily Coding Challenge GET route and the session-user payload must expose currentStreak, longestStreak, and lastCompletionDay.
- Recomputation must be idempotent: submitting the same day twice yields identical streak values.
- Streak values must never be negative, currentStreak must never exceed longestStreak, and both must be integers.

Compatibility requirements:
- Existing completion, session-user, and daily-coding-challenge response shapes must remain backward compatible; only additive fields are allowed and existing fields keep their meaning.
- Users with no completion history must report currentStreak 0, longestStreak 0, lastCompletionDay null.
- Existing tests for the challenge and daily-coding-challenge routes must continue to pass unchanged in intent.
- TypeBox...

Acceptance criteria:
- A pure streak-computation utility accepts a list of completion timestamps (or UTC day strings) and returns { currentStreak, longestStreak, lastCompletionDay } with the definitions in the issue.
- currentStreak counts consecutive UTC completion days ending today or yesterday; it is 0 when the latest completion day is older than yesterday.
- Duplicate completions within the same UTC day do not increase currentStreak or longestStreak.
- longestStreak equals the maximum consecutive run across all completion days and is always >= currentStreak.
- Empty history yields currentStreak 0, longestStreak 0, lastCompletionDay null.
- Submitting a Daily Coding Challenge completion recomputes and persists the streak fields and returns them in the completion response.
- The Daily Coding Challenge GET route and the session-user payload expose currentStreak, longestStreak, and lastCompletionDay validated by TypeBox schemas.
- Recomputation is idempotent: repeating a completion for a day already counted leaves streak values unchanged.
- All streak values serialize as non-negative integers (or null for lastCompletionDay) and pass schema validation.
- Existing response fields for completion, session user, and daily-coding-challenge routes are unchanged and their tests still pass.
