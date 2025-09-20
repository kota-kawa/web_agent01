"""
Test that simulates the problem where typing "箱根" (Hakone) might result in "長野" (Nagano) 
being selected due to autocomplete interference.

This test verifies that the clear flag properly prevents such interference.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, call
from automation.dsl.models import Selector, TypeAction
from vnc.executor import ActionPerformer


def test_hakone_nagano_autocomplete_issue():
    """Test the specific case where typing '箱根' should not result in '長野' being selected."""
    
    # This simulates the exact scenario from the problem statement
    action = TypeAction(
        selector=Selector(index=13),  # target with index 13 as in the example
        text="箱根",  # want to type Hakone
        clear=True   # clear flag is set
    )
    
    # Mock context
    mock_context = MagicMock()
    mock_context.config.action_timeout_ms = 5000
    
    performer = ActionPerformer(mock_context)
    
    # Mock the resolve method
    mock_resolved = MagicMock()
    mock_locator = MagicMock()
    mock_resolved.locator = mock_locator
    mock_resolved.stable_id = "search-input-13"
    
    performer._resolve = AsyncMock(return_value=mock_resolved)
    
    # Mock the _clear_and_type_carefully method to simulate proper clearing and typing
    async def mock_clear_and_type(locator, text):
        # This simulates the careful typing process that should prevent autocomplete interference
        assert text == "箱根"
        # In a real scenario, this would clear the field and type character by character
        pass
    
    performer._clear_and_type_carefully = AsyncMock(side_effect=mock_clear_and_type)
    
    async def run_test():
        result = await performer._type(action)
        
        # Verify that careful typing was used (which should prevent the Nagano issue)
        performer._clear_and_type_carefully.assert_called_once_with(mock_locator, "箱根")
        
        # Verify the action succeeded
        assert result.ok is True
        assert result.details["text"] == "箱根"
        assert result.details["cleared"] is True
        assert result.details["stable_id"] == "search-input-13"
    
    asyncio.run(run_test())


def test_clear_and_type_carefully_implementation():
    """Test the implementation details of the _clear_and_type_carefully method."""
    
    # Mock context and dependencies
    mock_context = MagicMock()
    mock_context.config.action_timeout_ms = 5000
    mock_context.page = MagicMock()
    
    performer = ActionPerformer(mock_context)
    
    # Mock locator and its methods
    mock_locator = MagicMock()
    mock_interactable = MagicMock()
    
    # Mock prepare_locator to return the interactable element
    from unittest.mock import patch
    
    async def run_test():
        with patch('vnc.executor.prepare_locator', new_callable=AsyncMock) as mock_prepare, \
             patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            
            mock_prepare.return_value = mock_interactable
            mock_interactable.click = AsyncMock()
            mock_interactable.fill = AsyncMock()
            mock_interactable.press = AsyncMock()
            mock_interactable.type = AsyncMock()
            mock_interactable.input_value = AsyncMock(return_value="箱根")
            
            # Call the method
            await performer._clear_and_type_carefully(mock_locator, "箱根")
            
            # Verify the clearing sequence
            assert mock_interactable.click.call_count >= 1
            assert mock_interactable.fill.call_count >= 1
            assert mock_interactable.press.call_count >= 2  # Control+a and Delete
            
            # Verify character-by-character typing was called
            expected_calls = [call("箱", delay=50), call("根", delay=50)]
            mock_interactable.type.assert_has_calls(expected_calls)
            
            # Verify sleep was called for timing delays
            assert mock_sleep.call_count >= 3  # Multiple sleep calls for timing
            
            # Verify input validation
            mock_interactable.input_value.assert_called()
    
    asyncio.run(run_test())


if __name__ == "__main__":
    test_hakone_nagano_autocomplete_issue()
    test_clear_and_type_carefully_implementation()
    print("All Hakone/Nagano autocomplete tests passed!")