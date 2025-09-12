# Duplicate Action Prevention Fix

## Problem Description

The web agent was experiencing an issue where it would repeatedly generate and execute the same action, particularly typing "箱根" into search fields multiple times. This caused an infinite loop where the same processing was repeated, preventing the agent from progressing to subsequent steps like clicking search buttons or setting dates.

### Specific Issue Example

```json
{
  "user": "箱根に９月の１３～１８まで大人１人で止まりたいので、１泊１万５千円以内のホテルを探して一番よさそうなものを教えて。ヤフートラベルで教えて",
  "bot": {
    "explanation": "Yahoo!トラベルで箱根のホテルを検索するために、まず検索キーワード入力欄に「箱根」を入力します。",
    "actions": [
      {
        "action": "type",
        "target": "input[aria-label=\"検索キーワードの入力\"]", 
        "value": "箱根"
      }
    ],
    "complete": false
  }
}
```

This same action was being repeated multiple times in consecutive turns instead of progressing to the next logical step.

## Root Cause Analysis

1. **Inadequate Loop Detection**: The original loop detection in `browser_executor.js` only checked for identical explanations, not identical actions.

2. **Insufficient History Checking**: The prompt didn't strongly emphasize checking conversation history for already-executed actions.

3. **No Action Tracking**: There was no mechanism to track and prevent repeating the exact same action on the same target.

## Solution Implementation

### 1. Enhanced JavaScript Loop Detection (`web/static/browser_executor.js`)

#### Added Action History Tracking
```javascript
// Enhanced loop detection: track actions, not just explanations
let actionHistory = [];
const MAX_ACTION_HISTORY = 5; // Keep track of last 5 actions
let identicalActionCount = 0;
const MAX_IDENTICAL_ACTIONS = 2; // Allow max 2 identical actions before stopping
```

#### Action Signature Creation
```javascript
// Create a signature for the actions to detect duplicates
const actionSignature = actions.map(a => `${a.action}:${a.target}:${a.value || ''}`).join('|');
```

#### Duplicate Detection and Prevention
```javascript
// Check if this exact sequence of actions was recently executed
const isIdenticalAction = actionHistory.some(histAction => histAction === actionSignature);

if (isIdenticalAction) {
  identicalActionCount += 1;
  console.warn(`Detected identical action sequence (${identicalActionCount}/${MAX_IDENTICAL_ACTIONS}): ${actionSignature}`);
  
  if (identicalActionCount >= MAX_IDENTICAL_ACTIONS) {
    console.warn("同一アクションが繰り返されたためループを終了します。");
    showSystemMessage("⚠️ 同じ操作の繰り返しを検出したため、タスクを終了します。");
    break;
  }
}
```

### 2. Enhanced Prompt Instructions (`agent/controller/prompt.py`)

#### Added Explicit History Checking Instructions
```python
- **履歴の詳細確認:** `## これまでの会話履歴` を**必ず詳細に確認**し、以下を特定します：
    - **既に実行済みのアクション**: どの要素に何を入力したか、どのボタンをクリックしたか、どのページに遷移したかを正確に把握
    - **入力済みの値**: フォームフィールドに既に入力された内容（例：検索キーワード「箱根」が既に入力済みかどうか）
    - **現在の進行状況**: タスクのどの段階まで完了しているか
- **重要**: 同じアクション（例：同じ要素への同じ値の入力）を**絶対に繰り返してはいけません**
```

#### Enhanced Duplicate Prevention Emphasis
```python
- **【最重要】履歴確認による重複防止**: アクションを実行する前に、必ず `## これまでの会話履歴` を確認し、同じアクション（同じtargetに同じvalueを入力するなど）が既に実行されていないかチェックしてください。既に実行済みの場合は、次のステップに進んでください。
```

## Testing and Validation

### 1. Comprehensive Test Suite

- **test_duplicate_action_fix.py**: Core functionality tests
- **test_progression_validation.py**: End-to-end flow validation  
- **demo_duplicate_fix.py**: Interactive demonstration

### 2. Test Results

All tests pass successfully:
- ✅ Action signature creation and uniqueness
- ✅ History tracking and conversation flow
- ✅ Multi-step progression without duplicates
- ✅ Prompt enhancement verification

### 3. Action Signature Examples

```
type:input[name='search']:箱根           # Search input
click:button[type='submit']:             # Search button  
click:input[name='checkin']:             # Date picker
type:input[name='search']:箱根           # DUPLICATE - would be detected
```

## Impact and Benefits

### Before Fix
```
Step 1: type '箱根' into input field
Step 2: type '箱根' into input field (DUPLICATE)
Step 3: type '箱根' into input field (DUPLICATE)
→ Infinite loop, no progress
```

### After Fix
```
Step 1: type '箱根' into input field
Step 2: click search button (logical next step)
Step 3: click date picker (logical progression)
→ Proper task completion
```

## Configuration Options

### JavaScript Parameters
- `MAX_ACTION_HISTORY`: Number of recent actions to track (default: 5)
- `MAX_IDENTICAL_ACTIONS`: Maximum allowed duplicate actions (default: 2)

### Prompt Enhancements
- Explicit history checking instructions
- Duplicate prevention emphasis
- Logical progression guidance

## Backward Compatibility

The fix maintains full backward compatibility:
- Existing functionality remains unchanged
- Original loop detection retained as secondary check
- No breaking changes to API or data structures

## Monitoring and Debugging

### Console Logging
```javascript
console.warn(`Detected identical action sequence (${count}/${max}): ${signature}`);
```

### User Notifications
```javascript
showSystemMessage("⚠️ 同じ操作の繰り返しを検出したため、タスクを終了します。");
```

### Debug Information
- Action signatures logged for troubleshooting
- History tracking visible in browser console
- Detailed error messages for developers

## Future Enhancements

1. **Dynamic Thresholds**: Adjust duplicate detection sensitivity based on action type
2. **Context Awareness**: Consider page state changes when detecting duplicates
3. **Recovery Strategies**: Automatic fallback actions when duplicates are detected
4. **Analytics**: Track duplicate detection patterns for optimization

## Usage

The fix is automatically active and requires no configuration. When the agent detects duplicate actions:

1. **Warning Logged**: Console warning with action signature
2. **Counter Incremented**: Tracks number of consecutive duplicates
3. **Automatic Termination**: Stops execution after threshold reached
4. **User Notification**: Clear message about duplicate detection

This ensures the agent progresses logically through tasks instead of getting stuck in infinite loops.