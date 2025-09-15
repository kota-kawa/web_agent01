#!/usr/bin/env python3
"""
Test for browser restart URL preservation functionality
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_browser_restart_preserves_url():
    """Test that browser restart preserves the current URL and doesn't fall back to Yahoo homepage"""
    print("Testing browser restart URL preservation...")
    
    test_url = "https://example.com/task-page"
    
    async def mock_test():
        # Use a module-level patch to properly mock the globals
        with patch('vnc.automation_server.PAGE') as mock_page_global, \
             patch('vnc.automation_server.BROWSER') as mock_browser_global, \
             patch('vnc.automation_server.PW') as mock_pw_global:
            
            # Setup mock PAGE with preserved URL
            mock_page_global.url = AsyncMock(return_value=test_url)
            mock_page_global.close = AsyncMock()
            mock_page_global.goto = AsyncMock()
            
            # Setup mock BROWSER and PW
            mock_browser_global.close = AsyncMock()
            mock_pw_global.stop = AsyncMock()
            
            # Create a new mock page for after init_browser
            new_mock_page = AsyncMock()
            new_mock_page.goto = AsyncMock()
            
            # Mock _init_browser and also set PAGE global in the module
            with patch('vnc.automation_server._init_browser') as mock_init:
                # After _init_browser is called, we need to simulate PAGE being set
                def mock_init_side_effect():
                    # Simulate what _init_browser does - set PAGE global
                    import vnc.automation_server as server_module
                    server_module.PAGE = new_mock_page
                
                mock_init.side_effect = mock_init_side_effect
                
                # Import and call the function
                from vnc.automation_server import _recreate_browser
                await _recreate_browser()
                
                # Verify that _init_browser was called
                mock_init.assert_called_once()
                
                # Verify that goto was called with the preserved URL
                assert new_mock_page.goto.call_count > 0, f"goto should have been called, but call count was {new_mock_page.goto.call_count}"
                
                # Get the call arguments to verify the URL
                call_args = new_mock_page.goto.call_args_list
                urls_called = [call[0][0] if call[0] else "no args" for call in call_args]
                assert test_url in urls_called, f"Expected {test_url} in goto calls, but got: {urls_called}"
                
                print("✓ Browser restart preserves URL correctly")
                print(f"✓ Preserved URL: {test_url}")
                print(f"✓ goto called {new_mock_page.goto.call_count} times with URLs: {urls_called}")
    
    # Run the async test
    asyncio.run(mock_test())


def test_browser_restart_skips_invalid_urls():
    """Test that browser restart skips invalid URLs like about: pages and default URL"""
    print("Testing browser restart skips invalid URLs...")
    
    # Import DEFAULT_URL
    from vnc.automation_server import DEFAULT_URL
    
    invalid_urls = [
        "about:blank",
        "about:newtab", 
        DEFAULT_URL,  # Should not restore default URL
        "",
    ]
    
    async def mock_test():
        for invalid_url in invalid_urls:
            with patch('vnc.automation_server.PAGE') as mock_page, \
                 patch('vnc.automation_server.BROWSER') as mock_browser, \
                 patch('vnc.automation_server.PW') as mock_pw:
                
                # Setup mock PAGE with invalid URL
                mock_page.url.return_value = invalid_url
                mock_page.close = AsyncMock()
                mock_page.goto = AsyncMock()
                
                # Setup mocks
                mock_browser.close = AsyncMock()
                mock_pw.stop = AsyncMock()
                
                # Mock _init_browser
                with patch('vnc.automation_server._init_browser') as mock_init:
                    mock_init.return_value = None
                    
                    # Import and call the function
                    from vnc.automation_server import _recreate_browser
                    await _recreate_browser()
                    
                    # Verify that _init_browser was called
                    mock_init.assert_called_once()
                    
                    # Verify that goto was NOT called for invalid URLs
                    assert mock_page.goto.call_count == 0, f"goto should not be called for invalid URL: {invalid_url}, but was called {mock_page.goto.call_count} times"
                    
                    print(f"✓ Correctly skipped invalid URL: {invalid_url}")
    
    asyncio.run(mock_test())


def test_browser_restart_handles_navigation_failure():
    """Test that browser restart handles navigation failure gracefully"""
    print("Testing browser restart handles navigation failure...")
    
    test_url = "https://example.com/task-page"
    
    async def mock_test():
        with patch('vnc.automation_server.PAGE') as mock_page_global, \
             patch('vnc.automation_server.BROWSER') as mock_browser_global, \
             patch('vnc.automation_server.PW') as mock_pw_global:
            
            # Setup mock PAGE with valid URL
            mock_page_global.url = AsyncMock(return_value=test_url)
            mock_page_global.close = AsyncMock()
            
            # Setup mocks
            mock_browser_global.close = AsyncMock()
            mock_pw_global.stop = AsyncMock()
            
            # Create a new mock page for after init_browser
            new_mock_page = AsyncMock()
            # Make goto fail for all attempts
            new_mock_page.goto = AsyncMock(side_effect=Exception("Navigation failed"))
            
            # Mock _init_browser
            with patch('vnc.automation_server._init_browser') as mock_init:
                # After _init_browser is called, set PAGE global
                def mock_init_side_effect():
                    import vnc.automation_server as server_module
                    server_module.PAGE = new_mock_page
                
                mock_init.side_effect = mock_init_side_effect
                
                # Import and call the function - should not raise exception
                from vnc.automation_server import _recreate_browser
                try:
                    await _recreate_browser()
                    print("✓ Browser restart handled navigation failure gracefully")
                except Exception as e:
                    assert False, f"Browser restart should handle navigation failure, but got: {e}"
                
                # Verify that goto was called multiple times (retry attempts)
                assert new_mock_page.goto.call_count > 1, f"Should have made multiple retry attempts, but only made {new_mock_page.goto.call_count}"
                print(f"✓ Made {new_mock_page.goto.call_count} retry attempts as expected")
    
    asyncio.run(mock_test())


if __name__ == "__main__":
    print("Running browser restart URL preservation tests...")
    print()
    
    try:
        test_browser_restart_preserves_url()
        print()
        test_browser_restart_skips_invalid_urls()
        print()
        test_browser_restart_handles_navigation_failure()
        print()
        print("🎉 All browser restart tests passed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)