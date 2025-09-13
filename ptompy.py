"""
Popup handling utilities for closing popups by clicking on blank areas.

This module provides functionality to detect popups and close them by clicking
on empty/blank areas of the page instead of using element selectors.

Example usage:

    # Basic popup closing
    from ptompy import close_popup_with_blank_click
    actions = close_popup_with_blank_click()
    
    # Simple blank area click  
    from ptompy import get_blank_click_action
    action = get_blank_click_action()
    
    # Use with basic actions
    from agent.actions.basic import close_popup, click_blank_area
    popup_close = close_popup()
    blank_click = click_blank_area()

The implementation works by:
1. Detecting common popup patterns using CSS selectors and z-index analysis
2. Finding blank/empty areas on the page that are safe to click
3. Performing coordinate-based clicks rather than element-based clicks
4. Providing fallback mechanisms when ideal blank areas aren't found

This approach is particularly useful for closing popups/modals that:
- Don't have obvious close buttons
- Have dynamic or obfuscated selectors
- Require clicking outside the popup area to dismiss
- Are created by third-party scripts with unpredictable markup
"""

from typing import Dict, List, Tuple, Optional
import logging

log = logging.getLogger(__name__)


def detect_popup() -> Dict:
    """
    Detect if there's a popup/modal/overlay currently displayed.
    
    Returns a dictionary with popup detection result and JavaScript to execute.
    """
    # JavaScript to detect common popup patterns
    popup_detection_script = """
    (function() {
        // Common popup/modal/overlay selectors
        const popupSelectors = [
            '[role="dialog"]',
            '[role="alertdialog"]', 
            '.modal',
            '.popup',
            '.overlay',
            '.dialog',
            '.modal-backdrop',
            '.modal-overlay',
            '[data-testid*="modal"]',
            '[data-testid*="popup"]',
            '[data-testid*="dialog"]',
            '[class*="modal"]',
            '[class*="popup"]',
            '[class*="overlay"]',
            '[class*="dialog"]'
        ];
        
        // Check for visible popups
        let foundPopups = [];
        
        for (let selector of popupSelectors) {
            const elements = document.querySelectorAll(selector);
            for (let el of elements) {
                const style = window.getComputedStyle(el);
                if (style.display !== 'none' && 
                    style.visibility !== 'hidden' && 
                    style.opacity !== '0' &&
                    el.offsetWidth > 0 && 
                    el.offsetHeight > 0) {
                    
                    foundPopups.push({
                        selector: selector,
                        element: el,
                        rect: el.getBoundingClientRect(),
                        zIndex: parseInt(style.zIndex) || 0
                    });
                }
            }
        }
        
        // Also check for elements with high z-index that might be popups
        const allElements = document.querySelectorAll('*');
        for (let el of allElements) {
            const style = window.getComputedStyle(el);
            const zIndex = parseInt(style.zIndex);
            if (zIndex >= 1000 && 
                style.position === 'fixed' &&
                style.display !== 'none' &&
                el.offsetWidth > 100 && 
                el.offsetHeight > 100) {
                
                foundPopups.push({
                    selector: 'high-z-index',
                    element: el,
                    rect: el.getBoundingClientRect(),
                    zIndex: zIndex
                });
            }
        }
        
        return foundPopups;
    })();
    """
    
    return {
        "action": "eval_js",
        "script": popup_detection_script
    }


def find_blank_area(viewport_width: int = 1920, viewport_height: int = 1080) -> Dict:
    """
    Find a blank area on the page where we can safely click to close popups.
    
    Args:
        viewport_width: Width of the browser viewport
        viewport_height: Height of the browser viewport
        
    Returns:
        Dictionary with action to find blank coordinates
    """
    
    blank_area_script = f"""
    (function() {{
        const viewportWidth = {viewport_width};
        const viewportHeight = {viewport_height};
        
        // Function to check if a point is "blank" (not covered by interactive elements)
        function isBlankPoint(x, y) {{
            const element = document.elementFromPoint(x, y);
            if (!element) return true;
            
            // Check if element is the body or html (essentially blank)
            if (element.tagName === 'BODY' || element.tagName === 'HTML') {{
                return true;
            }}
            
            // Check if element is a non-interactive container
            const style = window.getComputedStyle(element);
            const isInteractive = element.tagName.match(/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/) ||
                                 element.hasAttribute('onclick') ||
                                 element.hasAttribute('role') ||
                                 style.cursor === 'pointer';
            
            // If it's not interactive and has no meaningful content, consider it blank
            if (!isInteractive && (!element.textContent || element.textContent.trim() === '')) {{
                return true;
            }}
            
            return false;
        }}
        
        // Try various positions to find a blank area
        const candidates = [
            // Corners
            [50, 50],
            [viewportWidth - 50, 50],
            [50, viewportHeight - 50],
            [viewportWidth - 50, viewportHeight - 50],
            
            // Edges
            [viewportWidth / 2, 50],
            [viewportWidth / 2, viewportHeight - 50],
            [50, viewportHeight / 2],
            [viewportWidth - 50, viewportHeight / 2],
            
            // Center areas
            [viewportWidth / 2, viewportHeight / 2],
            [viewportWidth / 3, viewportHeight / 3],
            [2 * viewportWidth / 3, viewportHeight / 3],
            [viewportWidth / 3, 2 * viewportHeight / 3],
            [2 * viewportWidth / 3, 2 * viewportHeight / 3]
        ];
        
        for (let [x, y] of candidates) {{
            if (isBlankPoint(x, y)) {{
                return {{
                    found: true,
                    x: Math.round(x),
                    y: Math.round(y),
                    description: `Blank area at (${{Math.round(x)}}, ${{Math.round(y)}})`
                }};
            }}
        }}
        
        // If no perfect blank area found, try the safest corner
        return {{
            found: false,
            x: 50,
            y: 50,
            description: "Fallback to top-left corner"
        }};
    }})();
    """
    
    return {
        "action": "eval_js", 
        "script": blank_area_script
    }


def click_blank_area() -> List[Dict]:
    """
    Generate actions to click on a blank area to close popups.
    
    Returns:
        List of actions to execute for popup closing
    """
    return [
        # First detect if there are popups
        detect_popup(),
        
        # Find a blank area to click
        find_blank_area(),
        
        # The actual click will be handled by the click_at_coordinates action
        # which should be implemented in the automation server
    ]


def close_popup_with_blank_click() -> List[Dict]:
    """
    Main function to close popups by clicking on blank areas.
    
    This is the primary function that should be called when popups need to be closed.
    
    Returns:
        List of actions to detect popups and close them via blank area clicks
    """
    log.info("Attempting to close popup with blank area click")
    
    actions = []
    
    # Add popup detection
    actions.append(detect_popup())
    
    # Add blank area finding
    actions.append(find_blank_area())
    
    # Add a wait to ensure detection completes
    actions.append({
        "action": "wait",
        "ms": 500
    })
    
    # Add the blank area click action
    actions.append({
        "action": "click_blank_area"
    })
    
    return actions


def get_blank_click_action() -> Dict:
    """
    Get a simple blank area click action.
    
    Returns:
        Dictionary representing a blank area click action
    """
    return {"action": "click_blank_area"}


# Example usage functions for testing and demonstration
def example_popup_closing_workflow() -> List[Dict]:
    """
    Example workflow showing how to use popup closing functionality.
    
    Returns:
        List of actions demonstrating popup detection and closing
    """
    return [
        # Wait for page to stabilize
        {"action": "wait", "ms": 1000},
        
        # Detect and close any popups
        {"action": "close_popup"},
        
        # Alternative: just click blank area
        {"action": "click_blank_area"},
        
        # Wait to see effect
        {"action": "wait", "ms": 500}
    ]


def test_popup_functions():
    """Test that all functions return expected action dictionaries."""
    
    # Test basic action functions  
    blank_click = get_blank_click_action()
    assert blank_click["action"] == "click_blank_area"
    
    # Test detection functions return eval_js actions
    popup_detect = detect_popup()
    assert popup_detect["action"] == "eval_js"
    assert "script" in popup_detect
    
    blank_area = find_blank_area()
    assert blank_area["action"] == "eval_js"
    assert "script" in blank_area
    
    # Test workflow functions return lists
    workflow = close_popup_with_blank_click()
    assert isinstance(workflow, list)
    assert len(workflow) > 0
    
    blank_actions = click_blank_area()
    assert isinstance(blank_actions, list)
    
    example = example_popup_closing_workflow()
    assert isinstance(example, list)
    assert any(action["action"] == "close_popup" for action in example)
    
    print("All popup function tests passed!")
    

if __name__ == "__main__":
    test_popup_functions()