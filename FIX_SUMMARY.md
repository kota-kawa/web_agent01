"""
Fix Summary: Type Action Clear Flag Implementation

Problem:
--------
When the LLM specified a type action with clear=true to type "箱根" (Hakone) into a search field,
the system was allowing autocomplete interference to cause "長野" (Nagano) to be selected instead.

Root Cause:
-----------
The `clear` flag in TypeAction was being recorded in the result details but not actually being 
used to modify the typing behavior. The safe_fill function always cleared the field, but this 
wasn't sufficient to prevent autocomplete interference.

Solution:
---------
1. Modified the `_type` method in `vnc/executor.py` to check the `action.clear` flag
2. When `clear=True`, use a new method `_clear_and_type_carefully` instead of `safe_fill`
3. The new method implements these safeguards:
   - Thorough field clearing with multiple methods
   - Waiting for autocomplete suggestions to settle
   - Character-by-character typing with delays
   - Input verification and fallback mechanisms

Implementation Details:
----------------------
The `_clear_and_type_carefully` method:
1. Clicks the field and clears it with `fill("")`
2. Waits for autocomplete to settle (100ms)
3. Selects all content and deletes it (Control+a, Delete)
4. Waits again for autocomplete to settle
5. Types text character by character with 50ms delays between characters
6. Includes 20ms delays between characters for autocomplete to settle
7. Verifies the final text matches what was requested
8. Has fallback mechanisms if verification fails

Testing:
--------
Created comprehensive tests to verify:
- The clear flag properly triggers careful typing
- The specific "箱根" vs "長野" scenario is handled
- Existing functionality remains intact
- Action normalization works correctly

This fix ensures that autocomplete interference is minimized when `clear=true` is specified,
resolving the issue where the wrong location name was being selected.
"""