#!/usr/bin/env python3
"""
Test to validate prompt.py organization changes preserve functionality.
"""
import sys
import os

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)
os.environ['PYTHONPATH'] = project_root + ':' + os.environ.get('PYTHONPATH', '')

def test_prompt_functionality():
    """Test that the prompt module functions correctly."""
    print("Testing prompt.py functionality...")
    
    # Test 1: Import the module
    print("\n1. Testing module import...")
    try:
        from agent.controller.prompt import build_prompt
        print("✅ Module imports successfully")
    except Exception as e:
        print(f"❌ Module import failed: {e}")
        return False
    
    # Test 2: Create mock elements for testing
    print("\n2. Testing build_prompt function...")
    try:
        class MockDOMElement:
            def __init__(self):
                self.highlightIndex = 1
                self.tagName = "button"
                self.text = "Click me"
                self.attributes = {"id": "test-btn", "class": "btn-primary"}
                self.children = []
            
            def to_text(self, max_lines=None):
                return f"<{self.tagName}>{self.text}</{self.tagName}>"
        
        # Test basic prompt generation
        cmd = "Click the button"
        page = "<html><body><button>Click me</button></body></html>"
        hist = [{"user": "test", "bot": {"explanation": "test response"}}]
        
        prompt = build_prompt(cmd, page, hist)
        
        # Validate the prompt contains expected sections
        assert "あなたは、ブラウザタスクを自動化するために反復ループで動作するAIエージェントです" in prompt
        assert "思考と行動に関する厳格な指示" in prompt
        assert "目的の再確認" in prompt
        assert "状況分析" in prompt
        assert "次のアクションの検討" in prompt
        assert "JSON" in prompt
        assert "現在のページのDOMツリー" in prompt
        assert "これまでの会話履歴" in prompt
        assert "ユーザー命令" in prompt
        
        print("✅ build_prompt function works correctly")
        return True
        
    except Exception as e:
        print(f"❌ build_prompt function failed: {e}")
        return False

def get_original_system_prompt_content():
    """Extract the original system_prompt content for comparison."""
    from agent.controller.prompt import build_prompt
    
    # Create a test prompt to extract the system_prompt content
    cmd = "test"
    page = "<html><body>test</body></html>"
    hist = []
    
    prompt = build_prompt(cmd, page, hist)
    
    # Extract the system prompt part (everything before "現在のページのDOMツリー")
    system_prompt_end = prompt.find("--------------------------------\n---- 現在のページのDOMツリー ----")
    if system_prompt_end != -1:
        return prompt[:system_prompt_end].strip()
    else:
        # Fallback: find another delimiter
        system_prompt_end = prompt.find("## これまでの会話履歴")
        if system_prompt_end != -1:
            return prompt[:system_prompt_end].strip()
    
    return prompt

if __name__ == "__main__":
    success = test_prompt_functionality()
    if success:
        print("\n✅ All tests passed!")
        # Store the original content for later comparison
        original_content = get_original_system_prompt_content()
        print(f"\nOriginal system_prompt length: {len(original_content)} characters")
    else:
        print("\n❌ Tests failed!")
        sys.exit(1)