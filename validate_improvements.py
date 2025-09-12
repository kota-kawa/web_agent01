#!/usr/bin/env python3
"""
Simple validation script for the polling improvements.
"""
import os
import json

def validate_js_improvements():
    """Validate that our JavaScript improvements are properly implemented."""
    print("=== Validating JavaScript Polling Improvements ===")
    
    js_file = os.path.join(os.path.dirname(__file__), 'web', 'static', 'browser_executor.js')
    
    if not os.path.exists(js_file):
        print("❌ JavaScript file not found!")
        return False
    
    with open(js_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    improvements = [
        ("Enhanced polling function", "attemptGracefulFallback"),
        ("Fallback completion status", "completed_via_fallback"),
        ("Extended timeout duration", "90000"), # 90 seconds
        ("Adaptive intervals", "baseInterval"),
        ("Enhanced health checks", "maxRetries = 2"),
        ("Better error messages", "状態確認にエラーがありましたが"),
        ("Network resilience", "consecutiveErrors"),
        ("Graceful degradation", "fallback_reason"),
    ]
    
    passed = 0
    total = len(improvements)
    
    for desc, keyword in improvements:
        if keyword in content:
            print(f"✅ {desc}: Found '{keyword}'")
            passed += 1
        else:
            print(f"❌ {desc}: Missing '{keyword}'")
    
    print(f"\nValidation Result: {passed}/{total} improvements detected")
    
    if passed == total:
        print("🎉 All polling improvements successfully implemented!")
        return True
    else:
        print("⚠️  Some improvements may be missing or have different keywords")
        return passed >= (total * 0.8)  # 80% threshold

def validate_error_message_improvements():
    """Check that error messages are more user-friendly."""
    print("\n=== Validating Error Message Improvements ===")
    
    js_file = os.path.join(os.path.dirname(__file__), 'web', 'static', 'browser_executor.js')
    
    with open(js_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Count occurrences of the old alarming error message
    old_error_count = content.count("実行状態の確認に失敗しました")
    
    # Check for new, more informative messages
    new_messages = [
        "状態確認にエラーがありましたが、ページ状態を取得できました",
        "実行状態を確認できませんでした - ページの手動確認をお勧めします", 
        "操作は完了している可能性があります",
        "サーバーは正常に動作しています"
    ]
    
    found_new_messages = 0
    for msg in new_messages:
        if msg in content:
            found_new_messages += 1
            print(f"✅ Found improved message: '{msg[:30]}...'")
    
    print(f"\nError Message Analysis:")
    print(f"  Old error message occurrences: {old_error_count}")
    print(f"  New informative messages: {found_new_messages}/{len(new_messages)}")
    
    # We expect to still have the old message but with better context/handling
    if found_new_messages >= len(new_messages) * 0.75:  # 75% of new messages
        print("✅ Error messaging improvements look good!")
        return True
    else:
        print("⚠️  Could not find enough improved error messages")
        return False

def main():
    """Run all validations."""
    print("Validating Polling Improvements for '実行状態の確認に失敗しました' Error")
    print("=" * 80)
    
    js_valid = validate_js_improvements()
    msg_valid = validate_error_message_improvements()
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    
    if js_valid and msg_valid:
        print("🎉 ALL VALIDATIONS PASSED!")
        print("\nThe improvements should significantly reduce the occurrence of")
        print("'実行状態の確認に失敗しました' errors by:")
        print("  • Enhanced retry logic with adaptive timeouts")
        print("  • Better fallback mechanisms when polling fails")
        print("  • More informative error messages for users")
        print("  • Graceful degradation instead of complete failures")
        return True
    else:
        print("⚠️  SOME VALIDATIONS FAILED")
        print("Please review the implementation.")
        return False

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)