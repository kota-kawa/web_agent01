// intervention.js - Handle user intervention during AI execution

let currentTaskId = null;
let interventionCallback = null;

// UI Elements
const interventionPanel = document.getElementById("intervention-panel");
const interventionMessage = document.getElementById("intervention-message");
const interventionInput = document.getElementById("intervention-input");
const continueButton = document.getElementById("continue-button");
const skipButton = document.getElementById("skip-button");
const chatArea = document.getElementById("chat-area");

/**
 * Show user intervention panel
 * @param {string} taskId - Task ID waiting for intervention
 * @param {string} message - Message to show to user
 * @param {Function} callback - Callback to call when user provides intervention
 */
function showUserIntervention(taskId, message, callback) {
    currentTaskId = taskId;
    interventionCallback = callback;
    
    // Show the intervention panel
    interventionPanel.style.display = "block";
    interventionMessage.textContent = message;
    interventionInput.value = "";
    interventionInput.focus();
    
    // Add intervention indicator to chat
    const interventionIndicator = document.createElement("div");
    interventionIndicator.id = `intervention-indicator-${taskId}`;
    interventionIndicator.style.cssText = `
        margin: 10px 0;
        padding: 12px;
        background: linear-gradient(135deg, #fff3cd, #ffeaa7);
        border-left: 4px solid #ffc107;
        border-radius: 8px;
        font-weight: bold;
        color: #856404;
    `;
    interventionIndicator.innerHTML = `
        🔔 ユーザー確認待ち
        <div style="font-weight:normal;margin-top:5px;font-size:14px;">${message}</div>
    `;
    chatArea.appendChild(interventionIndicator);
    chatArea.scrollTop = chatArea.scrollHeight;
    
    console.log("User intervention requested for task:", taskId);
}

/**
 * Hide user intervention panel
 */
function hideUserIntervention() {
    interventionPanel.style.display = "none";
    currentTaskId = null;
    interventionCallback = null;
    
    // Remove intervention indicator from chat
    const indicators = document.querySelectorAll('[id^="intervention-indicator-"]');
    indicators.forEach(indicator => indicator.remove());
}

/**
 * Handle user intervention response
 * @param {string} userInput - User's intervention input
 */
async function handleUserIntervention(userInput) {
    if (!currentTaskId) {
        console.error("No active task for intervention");
        return;
    }
    
    try {
        // Send intervention to backend
        const response = await fetch("/intervention/provide", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                task_id: currentTaskId,
                user_input: userInput || "続行"
            })
        });
        
        const result = await response.json();
        
        if (result.status === "success") {
            // Show success message in chat
            const successMsg = document.createElement("p");
            successMsg.classList.add("system-message");
            successMsg.textContent = `✅ ユーザー介入を送信しました: "${userInput || "続行"}"`;
            successMsg.style.color = "#28a745";
            chatArea.appendChild(successMsg);
            chatArea.scrollTop = chatArea.scrollHeight;
            
            // Hide intervention panel
            hideUserIntervention();
            
            // Call callback if provided
            if (interventionCallback) {
                interventionCallback(userInput);
            }
            
            console.log("User intervention provided successfully");
        } else {
            throw new Error(result.message || "Failed to provide intervention");
        }
        
    } catch (error) {
        console.error("Error providing user intervention:", error);
        
        // Show error message
        const errorMsg = document.createElement("p");
        errorMsg.classList.add("system-message");
        errorMsg.textContent = `❌ 介入の送信に失敗しました: ${error.message}`;
        errorMsg.style.color = "#dc3545";
        chatArea.appendChild(errorMsg);
        chatArea.scrollTop = chatArea.scrollHeight;
    }
}

/**
 * Check if a response requires user intervention
 * @param {Object} response - LLM response
 * @returns {boolean} True if intervention is needed
 */
function requiresUserIntervention(response) {
    return response && (
        response.needs_user_intervention || 
        response.pause_for_user ||
        (response.status && response.status === "paused_for_user")
    );
}

/**
 * Handle automatic failure detection and user consultation
 * @param {string} taskId - Task ID
 * @param {number} failureCount - Number of consecutive failures
 */
function handleFailureConsultation(taskId, failureCount) {
    const message = `${failureCount}回の失敗が検出されました。どのように進めるべきかアドバイスをお願いします。`;
    showUserIntervention(taskId, message, (userInput) => {
        console.log("User advice provided after failures:", userInput);
    });
}

// Event listeners
if (continueButton) {
    continueButton.addEventListener("click", () => {
        const userInput = interventionInput.value.trim();
        handleUserIntervention(userInput);
    });
}

if (skipButton) {
    skipButton.addEventListener("click", () => {
        handleUserIntervention("スキップ");
    });
}

if (interventionInput) {
    interventionInput.addEventListener("keydown", (evt) => {
        if ((evt.ctrlKey || evt.metaKey) && evt.key === "Enter") {
            evt.preventDefault();
            const userInput = interventionInput.value.trim();
            handleUserIntervention(userInput);
        }
    });
}

// Export functions for use by other scripts
window.userIntervention = {
    show: showUserIntervention,
    hide: hideUserIntervention,
    handle: handleUserIntervention,
    requires: requiresUserIntervention,
    handleFailure: handleFailureConsultation
};