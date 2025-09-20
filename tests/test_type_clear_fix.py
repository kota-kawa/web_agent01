"""Test for the type action clear flag fix to prevent autocomplete interference."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from automation.dsl.models import Selector, TypeAction
from vnc.executor import ActionPerformer, ActionContext


def test_type_action_with_clear_flag():
    """Test that TypeAction with clear=True uses careful typing method."""
    
    # Create a mock TypeAction with clear=True
    action = TypeAction(
        selector=Selector(css="input[name=search]"),
        text="箱根",
        clear=True
    )
    
    # Mock the context and dependencies
    mock_context = MagicMock()
    mock_context.config.action_timeout_ms = 5000
    
    performer = ActionPerformer(mock_context)
    
    # Mock the resolve method
    mock_resolved = MagicMock()
    mock_resolved.locator = MagicMock()
    mock_resolved.stable_id = "test-input"
    
    performer._resolve = AsyncMock(return_value=mock_resolved)
    
    # Mock the careful typing method
    performer._clear_and_type_carefully = AsyncMock()
    
    # Run the _type method
    async def run_test():
        result = await performer._type(action)
        
        # Verify that the careful typing method was called
        performer._clear_and_type_carefully.assert_called_once()
        
        # Verify that the result indicates clearing was done
        assert result.details["cleared"] is True
        assert result.details["text"] == "箱根"
        assert result.ok is True
    
    # Run the async test
    asyncio.run(run_test())


def test_type_action_without_clear_flag():
    """Test that TypeAction with clear=False uses normal safe_fill."""
    
    # Create a mock TypeAction with clear=False
    action = TypeAction(
        selector=Selector(css="input[name=search]"),
        text="箱根",
        clear=False
    )
    
    # Mock the context and dependencies
    mock_context = MagicMock()
    mock_context.config.action_timeout_ms = 5000
    
    performer = ActionPerformer(mock_context)
    
    # Mock the resolve method
    mock_resolved = MagicMock()
    mock_resolved.locator = MagicMock()
    mock_resolved.stable_id = "test-input"
    
    performer._resolve = AsyncMock(return_value=mock_resolved)
    
    # Mock safe_fill
    with patch('vnc.executor.safe_fill', new_callable=AsyncMock) as mock_safe_fill:
        # Run the _type method
        async def run_test():
            result = await performer._type(action)
            
            # Verify that safe_fill was called
            mock_safe_fill.assert_called_once()
            
            # Verify that the result does not indicate clearing if clear is False
            # The key point is that normal safe_fill should be used
            assert result.details["text"] == "箱根"
            assert result.ok is True
        
        # Run the async test
        asyncio.run(run_test())


if __name__ == "__main__":
    test_type_action_with_clear_flag()
    test_type_action_without_clear_flag()
    print("All tests passed!")