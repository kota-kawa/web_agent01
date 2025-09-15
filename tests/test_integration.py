#!/usr/bin/env python3
"""
Integration test for the enhanced DOM with prompt building
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from agent.browser.dom import DOMElementNode
from agent.controller.prompt import build_prompt

def test_prompt_integration():
    """Test that the enhanced DOM works with the prompt building system"""
    print("Testing prompt integration...")
    
    # Create a realistic DOM structure
    mock_dom_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "isInteractive": False,
        "children": [
            {
                "tagName": "form",
                "attributes": {"id": "login-form"},
                "xpath": "/html/body/form[1]",
                "isVisible": True,
                "isInteractive": False,
                "children": [
                    {
                        "tagName": "input",
                        "attributes": {
                            "type": "text",
                            "name": "username",
                            "placeholder": "ユーザー名"
                        },
                        "xpath": "/html/body/form[1]/input[1]",
                        "isVisible": True,
                        "isInteractive": True,
                        "highlightIndex": 1,
                        "children": []
                    },
                    {
                        "tagName": "input",
                        "attributes": {
                            "type": "password",
                            "name": "password",
                            "placeholder": "パスワード"
                        },
                        "xpath": "/html/body/form[1]/input[2]",
                        "isVisible": True,
                        "isInteractive": True,
                        "highlightIndex": 2,
                        "children": []
                    },
                    {
                        "tagName": "button",
                        "attributes": {
                            "type": "submit",
                            "title": "ログイン"
                        },
                        "xpath": "/html/body/form[1]/button[1]",
                        "isVisible": True,
                        "isInteractive": True,
                        "highlightIndex": 3,
                        "children": [
                            {
                                "nodeType": "text",
                                "text": "ログイン"
                            }
                        ]
                    }
                ]
            }
        ]
    }
    
    # Create the DOM object
    dom = DOMElementNode.from_json(mock_dom_data)
    
    # Test building a prompt with the new DOM
    cmd = "ログインフォームに入力してください"
    page = "<html><body><form id='login-form'>...</form></body></html>"
    hist = []
    
    try:
        prompt = build_prompt(
            cmd=cmd,
            page=page,
            hist=hist,
            screenshot=False,
            elements=dom,
            error=None
        )
        
        # Verify the prompt contains our structured DOM output
        assert "[1]" in prompt  # Interactive element numbering
        assert "[2]" in prompt
        assert "[3]" in prompt
        assert "ユーザー名" in prompt  # Japanese placeholders
        assert "パスワード" in prompt
        assert "ログイン" in prompt
        
        # Verify that the structured format is used instead of raw HTML
        assert 'placeholder="ユーザー名"' in prompt
        assert 'type="text"' in prompt
        assert 'type="password"' in prompt
        
        print("✓ Prompt integration works correctly")
        print("✓ DOM structured output is included in prompt")
        print("✓ Interactive element numbering is present")
        print("✓ Japanese text is preserved correctly")
        
        return True
        
    except Exception as e:
        print(f"✗ Prompt integration failed: {e}")
        return False

def test_prompt_with_scroll_info():
    """Test prompt integration with scroll position information"""
    print("\nTesting prompt with scroll information...")
    
    mock_dom_data = {
        "tagName": "body",
        "attributes": {},
        "xpath": "/html/body",
        "isVisible": True,
        "isInteractive": False,
        "children": [
            {
                "tagName": "div",
                "attributes": {"class": "content"},
                "xpath": "/html/body/div[1]",
                "isVisible": True,
                "isInteractive": False,
                "annotations": ["SCROLL"],
                "children": [
                    {
                        "nodeType": "text",
                        "text": "Long scrollable content"
                    }
                ]
            }
        ]
    }
    
    dom = DOMElementNode.from_json(mock_dom_data)
    dom.set_scroll_info(pixels_above=100, pixels_below=200)
    
    try:
        prompt = build_prompt(
            cmd="スクロールして情報を確認してください",
            page="<html>...</html>",
            hist=[],
            screenshot=False,
            elements=dom,
            error=None
        )
        
        # Verify scroll information is in the prompt
        assert "... 100 pixels above ..." in prompt
        assert "... 200 pixels below ..." in prompt
        assert "|SCROLL|" in prompt
        
        print("✓ Scroll position information included in prompt")
        print("✓ Visual annotations are preserved")
        
        return True
        
    except Exception as e:
        print(f"✗ Scroll info integration failed: {e}")
        return False

if __name__ == "__main__":
    print("Running DOM-Prompt integration tests...\n")
    
    success1 = test_prompt_integration()
    success2 = test_prompt_with_scroll_info()
    
    if success1 and success2:
        print("\n🎉 All integration tests passed! The enhanced DOM system is fully integrated.")
    else:
        print("\n❌ Some integration tests failed.")
        sys.exit(1)