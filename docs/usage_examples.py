"""Usage examples for the Element Catalog System."""

# Example 1: Basic Index-based Targeting

## Before (CSS/XPath selectors)
actions = [
    {"action": "navigate", "target": "https://example.com/login"},
    {"action": "type", "target": "css=input[name='username']", "value": "user@example.com"},
    {"action": "type", "target": "css=input[name='password']", "value": "password123"},
    {"action": "click", "target": "css=button[type='submit']"}
]

## After (Index-based targeting)
actions = [
    {"action": "navigate", "target": "https://example.com/login"},
    {"action": "refresh_catalog"},  # Generate element catalog
    {"action": "type", "target": "index=0", "value": "user@example.com"},  # Username field
    {"action": "type", "target": "index=1", "value": "password123"},       # Password field  
    {"action": "click", "target": "index=2"}                               # Submit button
]

# Example 2: Error Handling with Catalog

def execute_with_error_handling(actions, expected_catalog_version=None):
    """Execute actions with proper error handling."""
    result = execute_dsl(
        {"actions": actions}, 
        expected_catalog_version=expected_catalog_version
    )
    
    if not result["success"]:
        error_code = result["error"]["code"]
        
        if error_code == "CATALOG_OUTDATED":
            # Refresh catalog and retry
            refresh_result = execute_dsl({"actions": [{"action": "refresh_catalog"}]})
            if refresh_result["success"]:
                # Get new catalog version and retry
                new_version = refresh_result["observation"]["catalog_version"]
                return execute_dsl(
                    {"actions": actions}, 
                    expected_catalog_version=new_version
                )
        
        elif error_code == "ELEMENT_NOT_FOUND":
            # Try scrolling to find element
            scroll_actions = [
                {"action": "scroll_to_text", "target": "Submit"},
                {"action": "refresh_catalog"}
            ]
            scroll_result = execute_dsl({"actions": scroll_actions})
            if scroll_result["success"]:
                # Retry original actions
                new_version = scroll_result["observation"]["catalog_version"]
                return execute_dsl(
                    {"actions": actions}, 
                    expected_catalog_version=new_version
                )
    
    return result

# Example 3: LLM Integration with Catalog

def build_prompt_with_catalog(command, page_html, conversation_history):
    """Build LLM prompt including element catalog information."""
    
    # Get current catalog
    catalog_data = get_catalog()
    
    if catalog_data and INDEX_MODE:
        catalog_display = format_catalog_for_display(catalog_data)
        
        prompt = f"""
Task: {command}

Current Page Elements:
{catalog_display}

Instructions:
1. Use index=N to target elements (e.g., index=0 for first element)
2. If element not found, use scroll_to_text then refresh_catalog
3. Handle CATALOG_OUTDATED errors by executing refresh_catalog
4. Prefer index targeting over CSS selectors

Previous Actions: {conversation_history}
Current Page: {page_html}

Generate actions to complete the task.
"""
    else:
        # Fallback to CSS/XPath mode
        prompt = f"""
Task: {command}
Use CSS selectors or XPath for element targeting.
Current Page: {page_html}
"""
    
    return prompt

# Example 4: Enhanced Wait Actions

# Wait for network to be idle
wait_network = {"action": "wait", "until": "network_idle", "ms": 3000}

# Wait for specific element to appear
wait_element = {"action": "wait", "until": "selector", "target": "css=.loading", "ms": 5000}

# Simple timeout wait
wait_timeout = {"action": "wait", "until": "timeout", "ms": 2000}

# Example 5: Complete Form Filling with Error Recovery

def fill_registration_form(user_data):
    """Complete form filling example with error recovery."""
    
    # Step 1: Navigate and generate catalog
    initial_actions = [
        {"action": "navigate", "target": "https://example.com/register"},
        {"action": "wait", "until": "network_idle", "ms": 2000},
        {"action": "refresh_catalog"}
    ]
    
    result = execute_dsl({"actions": initial_actions})
    if not result["success"]:
        raise Exception(f"Failed to load page: {result['error']['message']}")
    
    catalog_version = result["observation"]["catalog_version"]
    
    # Step 2: Fill form using index targeting
    form_actions = [
        {"action": "type", "target": "index=0", "value": user_data["first_name"]},
        {"action": "type", "target": "index=1", "value": user_data["last_name"]},
        {"action": "type", "target": "index=2", "value": user_data["email"]},
        {"action": "type", "target": "index=3", "value": user_data["password"]},
        {"action": "click", "target": "index=4"}  # Submit button
    ]
    
    # Execute with error handling
    result = execute_with_error_handling(form_actions, catalog_version)
    
    if result["success"]:
        print("Form submitted successfully!")
        if result["observation"]["nav_detected"]:
            print("Navigation detected - form submission likely successful")
    else:
        print(f"Form submission failed: {result['error']['message']}")
    
    return result

# Example 6: Search and Selection Flow

def search_and_select(search_term, selection_criteria):
    """Example of search and selection with catalog."""
    
    actions = [
        # Refresh catalog to get current elements
        {"action": "refresh_catalog"},
        
        # Type in search box (assuming it's the first input)
        {"action": "type", "target": "index=0", "value": search_term},
        
        # Click search button (assuming it's the first button)
        {"action": "click", "target": "index=1"},
        
        # Wait for results to load
        {"action": "wait", "until": "network_idle", "ms": 3000},
        
        # Refresh catalog to get new search results
        {"action": "refresh_catalog"},
        
        # Scroll to find specific result if needed
        {"action": "scroll_to_text", "target": selection_criteria},
        
        # Refresh catalog after scrolling
        {"action": "refresh_catalog"},
        
        # Click on the result (index would need to be determined from catalog)
        {"action": "click", "target": "index=5"}  # Example index
    ]
    
    return execute_dsl({"actions": actions})

# Example 7: Configuration and Feature Detection

import os

def check_catalog_support():
    """Check if element catalog features are available."""
    
    # Check if INDEX_MODE is enabled
    index_mode = os.getenv("INDEX_MODE", "true").lower() == "true"
    
    if not index_mode:
        print("INDEX_MODE is disabled - using CSS/XPath fallback")
        return False
    
    # Test catalog generation
    try:
        catalog_data = get_catalog()
        if catalog_data and "error" not in catalog_data:
            print(f"Catalog available with {len(catalog_data.get('abbreviated_view', []))} elements")
            return True
        else:
            print("Catalog not available - check automation server")
            return False
    except Exception as e:
        print(f"Error checking catalog: {e}")
        return False

# Example 8: Adaptive Action Generation

def generate_adaptive_actions(task_description, page_context):
    """Generate actions that adapt based on available features."""
    
    if check_catalog_support():
        # Use index-based approach
        return [
            {"action": "refresh_catalog"},
            {"action": "click", "target": "index=0"},  # Would be determined by LLM
            {"action": "type", "target": "index=1", "value": "search term"}
        ]
    else:
        # Fallback to CSS selectors
        return [
            {"action": "click", "target": "css=button.search-btn"},
            {"action": "type", "target": "css=input[name='q']", "value": "search term"}
        ]

# Example 9: Catalog Information Display

def display_catalog_info(catalog_data):
    """Display catalog information in a readable format."""
    
    if not catalog_data or "error" in catalog_data:
        print("No catalog available")
        return
    
    print(f"=== Element Catalog (v{catalog_data['catalog_version']}) ===")
    print(f"Page: {catalog_data['title']}")
    print(f"URL: {catalog_data['url']}")
    
    if catalog_data.get('short_summary'):
        print(f"Summary: {catalog_data['short_summary']}")
    
    print("\nElements:")
    
    current_section = None
    for element in catalog_data.get('abbreviated_view', []):
        # Group by section
        section = element.get('section_hint', '')
        if section and section != current_section:
            print(f"\n--- {section.upper()} ---")
            current_section = section
        
        # Format element info
        label = element.get('primary_label', '')
        secondary = element.get('secondary_label', '')
        state = element.get('state_hint', '')
        href = element.get('href_short', '')
        
        info_parts = [label]
        if secondary:
            info_parts.append(f"({secondary})")
        if href:
            info_parts.append(f"→{href}")
        if state:
            info_parts.append(f"[{state}]")
        
        info = " ".join(info_parts)
        print(f"  [{element['index']}] {element['role']}: {info}")
    
    print(f"\nUse index=N to target elements (e.g., index=0)")

# Example usage:
if __name__ == "__main__":
    # Check if catalog system is available
    if check_catalog_support():
        # Use the new index-based system
        user_data = {
            "first_name": "John",
            "last_name": "Doe", 
            "email": "john.doe@example.com",
            "password": "secure123"
        }
        
        result = fill_registration_form(user_data)
        print(f"Registration result: {result['success']}")
    else:
        print("Using fallback CSS/XPath system")