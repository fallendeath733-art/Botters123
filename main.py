# Main entry point - versi ringkas
# Semua kelas dan fungsi ada di core.py

from core import *
import os
import asyncio
import sys

def cleanup_async_tasks_and_websockets():
    """Properly cleanup all async tasks and WebSocket connections"""
    try:
        # Get or create event loop
        loop = None
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Cancel all pending tasks first
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to cancel (with short timeout)
        if tasks:
            try:
                loop.run_until_complete(asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=1.5
                ))
            except (asyncio.TimeoutError, Exception):
                pass  # Ignore timeout/errors during cancellation
        
        # Stop WebSocket streams using dynamic manager
        from core import _init_ws_manager
        ws_manager = _init_ws_manager()
        if ws_manager:
            try:
                loop.run_until_complete(asyncio.wait_for(
                    ws_manager.stop_all_streams(),
                    timeout=2.0
                ))
            except (asyncio.TimeoutError, Exception):
                pass  # Ignore timeout/errors
        
        # Cleanup async tasks
        try:
            loop.run_until_complete(asyncio.wait_for(
                task_manager.cleanup_all_resources(),
                timeout=1.5
            ))
        except (asyncio.TimeoutError, Exception):
            pass  # Ignore timeout/errors
        
        return True
    except Exception as e:
        return False

def select_exchange():
    """Display exchange selection menu and update config"""
    global SELECTED_EXCHANGE
    
    # Check AUTO_CONTINUE early
    auto_continue_env = os.getenv("AUTO_CONTINUE", "0")
    AUTO_CONTINUE = auto_continue_env == "1"
    
    # Store original exchange before loading config
    # SELECTED_EXCHANGE is imported from core via "from core import *"
    original_exchange = SELECTED_EXCHANGE if 'SELECTED_EXCHANGE' in globals() else None
    
    # Load config to get exchange from config.txt (for display only)
    # But preserve the original if it was set via environment variable
    # Note: load_config might not exist, so we'll handle it gracefully
    try:
        import core
        if hasattr(core, 'load_config'):
            core.load_config(preserve_selected_exchange=False)
    except:
        pass  # Config loading is optional
    
    # If original was set (e.g., from env var), keep it
    # SELECTED_EXCHANGE is imported from core via "from core import *"
    if 'SELECTED_EXCHANGE' in globals():
        if original_exchange and original_exchange != SELECTED_EXCHANGE:
            # Check if original was from env var
            env_exchange = os.getenv("SELECTED_EXCHANGE")
            if env_exchange and env_exchange.upper() == original_exchange:
                SELECTED_EXCHANGE = original_exchange
    
    # Get supported exchanges
    try:
        from exchanger import ExchangeFactory
        supported_exchanges = ExchangeFactory.get_supported_exchanges()
    except Exception:
        supported_exchanges = ['BYBIT', 'BINANCE', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO']
    
    # SELECTED_EXCHANGE is imported from core via "from core import *"
    current_exchange = SELECTED_EXCHANGE if 'SELECTED_EXCHANGE' in globals() else None
    
    # Display menu with better styling
    print(f"\n{WHITE}Select Exchange{RESET}")
    
    # Display exchange options
    exchange_map = {}
    for idx, exchange in enumerate(supported_exchanges, 1):
        exchange_map[str(idx)] = exchange
        if exchange == current_exchange:
            print(f"{WHITE}{idx}. {exchange}{RESET}")
        else:
            print(f"{WHITE}{idx}. {exchange}{RESET}")
    
    # Check if exchange is set via environment variable
    env_exchange = os.getenv("SELECTED_EXCHANGE")
    if env_exchange:
        print(f"{CYAN}💡 Environment variable SELECTED_EXCHANGE={env_exchange} detected{RESET}")
        print(f"{YELLOW}⚠️  Environment variable takes precedence. Change it or clear to use interactive selection.{RESET}\n")
        
        # Auto-accept if AUTO_CONTINUE is enabled
        if AUTO_CONTINUE:
            print(f"{CYAN}AUTO_CONTINUE enabled - Auto-accepting environment variable{RESET}")
            SELECTED_EXCHANGE = env_exchange.upper()
            return SELECTED_EXCHANGE
        
        try:
            use_env = input(f"{YELLOW}Use environment variable? (Y/n): {RESET}").strip().lower()
            if use_env != 'n':
                SELECTED_EXCHANGE = env_exchange.upper()
                return SELECTED_EXCHANGE
        except (EOFError, KeyboardInterrupt):
            print(f"\n{YELLOW}⚠️ Input cancelled. Using environment variable.{RESET}")
            SELECTED_EXCHANGE = env_exchange.upper()
            return SELECTED_EXCHANGE
    
    # Get user selection
    while True:
        choice = input(f"{WHITE}Select option (1-{len(supported_exchanges)}) : {RESET}").strip()
        
        if not choice:
            # Empty input - show error and ask again
            print(f"{RED}❌ Invalid selection. Please enter a number (1-{len(supported_exchanges)}){RESET}")
            continue
        
        if choice in exchange_map:
            selected = exchange_map[choice]
            # IMPORTANT: Set SELECTED_EXCHANGE BEFORE updating config.txt
            SELECTED_EXCHANGE = selected
            
            # Update config.txt to persist the selection
            try:
                update_config_exchange(selected)
            except Exception as e:
                print(f"{YELLOW}⚠️  Could not update config.txt: {e}{RESET}")
                print(f"{CYAN}💡 Using exchange for this session only{RESET}")
            
            # Display selected exchange
            print(f"{GREEN}Selected Exchange: {selected}{RESET}")
            
            # Return the selected exchange
            return selected
        else:
            print(f"{RED}❌ Invalid choice. Please select 1-{len(supported_exchanges)}{RESET}")

def update_config_exchange(exchange_name: str):
    """Update SELECTED_EXCHANGE in config.txt"""
    config_path = CONFIG_FILE
    if not os.path.exists(config_path):
        print(f"{YELLOW}⚠️  Config file not found: {config_path}{RESET}")
        return
    
    try:
        with open(config_path, 'r') as f:
            lines = f.readlines()
        
        # Update or add SELECTED_EXCHANGE
        updated = False
        new_lines = []
        for line in lines:
            if line.strip().startswith('SELECTED_EXCHANGE='):
                new_lines.append(f"SELECTED_EXCHANGE={exchange_name}\n")
                updated = True
            else:
                new_lines.append(line)
        
        # If not found, add after the header comment
        if not updated:
            # Find a good place to insert (after header comments)
            insert_idx = 0
            for i, line in enumerate(new_lines):
                if line.strip() and not line.strip().startswith('#'):
                    insert_idx = i
                    break
            new_lines.insert(insert_idx, f"SELECTED_EXCHANGE={exchange_name}\n")
        
        with open(config_path, 'w') as f:
            f.writelines(new_lines)
    except Exception as e:
        raise Exception(f"Failed to update config: {e}")

# Main function dan entry point
async def main_hybrid_enhanced():
    # Ensure color constants and output_manager are available
    from core import CYAN, RED, GREEN, YELLOW, WHITE, MAGENTA, RESET, output_manager, error_handler
    
    # Select exchange first
    exchange_name = select_exchange()
    
    # Ensure SELECTED_EXCHANGE is set to the selected exchange
    # This should already be set by select_exchange(), but we double-check
    global SELECTED_EXCHANGE
    if SELECTED_EXCHANGE != exchange_name:
        SELECTED_EXCHANGE = exchange_name
        print(f"{YELLOW}⚠️  Warning: SELECTED_EXCHANGE mismatch, corrected to {exchange_name}{RESET}")
    
    # IMPORTANT: Update core.SELECTED_EXCHANGE to match the selected exchange
    import core
    core.SELECTED_EXCHANGE = exchange_name
    
    # Reload config but preserve the selected exchange (don't override with config.txt)
    # This loads API credentials without changing SELECTED_EXCHANGE
    core.load_config(preserve_selected_exchange=True)
    
    # Force reset exchange client to use newly selected exchange
    core._exchange_client = None
    core._last_selected_exchange = None
    core._ws_manager = None
    core._last_selected_ws_exchange = None
    
    # Display account information after exchange selection
    try:
        # Ensure CONFIG is loaded before getting account info
        # Import _init_exchange_client and ensure color constants are available
        from core import _init_exchange_client
        # Ensure color constants are available (they should be from 'from core import *')
        # But if they're not, import them explicitly
        try:
            # Test if RESET is available
            _ = RESET
        except NameError:
            from core import RESET, YELLOW, GREEN, WHITE
        
        # CONFIG should already be loaded above, but ensure it's loaded
        if not core.CONFIG:
            core.load_config(preserve_selected_exchange=True)
        exchange_client = _init_exchange_client()
        if exchange_client:
            # Check if testnet mode before calling get_account_info
            is_testnet = False
            if core.CONFIG and exchange_name in core.CONFIG:
                testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
                is_testnet = str(testnet).lower() == 'true'
            
            account_info = exchange_client.get_account_info()
            
            # Check if API is connected and has valid account data
            # Only show account info if we have actual balance data
            has_valid_data = False
            if account_info:
                spot_balances = account_info.get('spot_balances', [])
                usdt_balance = account_info.get('usdt_balance', 0)
                # Consider valid if we have spot balances (non-empty list) or USDT balance > 0
                if (spot_balances and len(spot_balances) > 0) or (usdt_balance and usdt_balance > 0):
                    has_valid_data = True
            
            # Only show account information if we have valid data (not in testnet mode if no data)
            # For testnet mode, we still show account info even if no data
            if has_valid_data:
                # Get account type
                account_type = "SPOT"
                if account_info:
                    account_type = account_info.get('account_type', 'SPOT')
                
                output_manager.print_static(f"\n{WHITE}Account information ({account_type}){RESET}")
                
                if account_info:
                    # Display all spot balances
                    spot_balances = account_info.get('spot_balances', [])
                    if spot_balances:
                        # Calculate dynamic widths based on actual values
                        max_coin_len = max(len('Coin'), max(len(b.get('coin', '')) for b in spot_balances))
                        max_total_len = len('Total')
                        max_available_len = len('Available')
                        
                        # Calculate max length for numbers
                        for balance in spot_balances:
                            wallet_balance = balance.get('wallet_balance', 0)
                            available = balance.get('available', 0)
                            
                            # Format to estimate length
                            if wallet_balance < 0.0001:
                                balance_str = f"{wallet_balance:.8f}".rstrip('0').rstrip('.')
                            elif wallet_balance < 1:
                                balance_str = f"{wallet_balance:.6f}".rstrip('0').rstrip('.')
                            elif wallet_balance < 1000:
                                balance_str = f"{wallet_balance:.4f}".rstrip('0').rstrip('.')
                            else:
                                balance_str = f"{wallet_balance:,.2f}"
                            
                            if available < 0.0001:
                                available_str = f"{available:.8f}".rstrip('0').rstrip('.')
                            elif available < 1:
                                available_str = f"{available:.6f}".rstrip('0').rstrip('.')
                            else:
                                available_str = f"{available:.4f}".rstrip('0').rstrip('.')
                            
                            max_total_len = max(max_total_len, len(balance_str))
                            max_available_len = max(max_available_len, len(available_str))
                        
                        # Add padding for readability (minimum widths)
                        coin_width = max(max_coin_len, 8)
                        total_width = max(max_total_len, len('Total'))
                        available_width = max(max_available_len, len('Available'))
                        
                        # Ensure consistent spacing - make sure Total and Available columns are same width for alignment
                        num_width = max(total_width, available_width)
                        
                        # Compact header with proper alignment
                        output_manager.print_static(f"{WHITE}{'Coin':<{coin_width}}  {'Total':>{num_width}}  {'Available':>{num_width}}{RESET}")
                        output_manager.print_static(f"{WHITE}{'─'*(coin_width + num_width * 2 + 4)}{RESET}")
                        
                        for balance in spot_balances:
                            coin_name = balance.get('coin', '')
                            wallet_balance = balance.get('wallet_balance', 0)
                            available = balance.get('available', 0)
                            
                            # Format balance based on value
                            if wallet_balance < 0.0001:
                                # Very small amounts - show 8 decimals
                                balance_str = f"{wallet_balance:.8f}".rstrip('0').rstrip('.')
                            elif wallet_balance < 1:
                                # Small amounts - show 6 decimals
                                balance_str = f"{wallet_balance:.6f}".rstrip('0').rstrip('.')
                            elif wallet_balance < 1000:
                                # Medium amounts - show 4 decimals
                                balance_str = f"{wallet_balance:.4f}".rstrip('0').rstrip('.')
                            else:
                                # Large amounts - show 2 decimals with comma
                                balance_str = f"{wallet_balance:,.2f}"
                            
                            # Format available balance
                            if available < 0.0001:
                                available_str = f"{available:.8f}".rstrip('0').rstrip('.')
                            elif available < 1:
                                available_str = f"{available:.6f}".rstrip('0').rstrip('.')
                            else:
                                available_str = f"{available:.4f}".rstrip('0').rstrip('.')
                            
                            # Format with consistent alignment - both numbers use same width
                            output_manager.print_static(f"{WHITE}{coin_name:<{coin_width}}  {GREEN}{balance_str:>{num_width}}  {available_str:>{num_width}}{RESET}")
                        
                        # Show summary
                        output_manager.print_static(f"\n{WHITE}Total Assets: {GREEN}{len(spot_balances)} coin(s){RESET}")
                        
                        # Calculate total USDT value by converting all coins to USDT
                        usdt_balance = account_info.get('usdt_balance', 0)
                        total_usdt_value = usdt_balance  # Start with USDT balance
                        
                        # Convert all other coins to USDT value
                        try:
                            from core import _init_exchange_client
                            exchange_client = _init_exchange_client()
                            
                            if exchange_client:
                                # Get prices for all coins (except USDT)
                                for balance in spot_balances:
                                    coin_name = balance.get('coin', '')
                                    if coin_name.upper() == 'USDT':
                                        continue  # Skip USDT, already counted
                                    
                                    wallet_balance = balance.get('wallet_balance', 0)
                                    if wallet_balance <= 0:
                                        continue
                                    
                                    # Try to get price using REST API (sync)
                                    try:
                                        symbol = f"{coin_name}_USDT"
                                        price = exchange_client.get_price_rest(symbol)
                                        
                                        if price and price > 0:
                                            coin_value_usdt = wallet_balance * price
                                            total_usdt_value += coin_value_usdt
                                    except Exception:
                                        # If price fetch fails, skip this coin
                                        pass
                        except Exception:
                            # If conversion fails, just use USDT balance
                            pass
                        
                        # Show total USDT value
                        if total_usdt_value > 0:
                            if total_usdt_value < 1:
                                output_manager.print_static(f"{WHITE}Total USDT Value: {GREEN}${total_usdt_value:.8f}{RESET}\n")
                            else:
                                output_manager.print_static(f"{WHITE}Total USDT Value: {GREEN}${total_usdt_value:,.2f}{RESET}\n")
                    else:
                        # Fallback to USDT only if spot_balances not available
                        usdt_balance = account_info.get('usdt_balance', 0)
                        if usdt_balance > 0:
                            if usdt_balance < 1:
                                output_manager.print_static(f"{GREEN}USDT Balance: ${usdt_balance:.8f}{RESET}")
                            else:
                                output_manager.print_static(f"{GREEN}USDT Balance: ${usdt_balance:,.2f}{RESET}")
                        else:
                            output_manager.print_static(f"{YELLOW}USDT Balance: $0.00 (No balance){RESET}")
                else:
                    # Account info exists but no balances to show
                    pass
            else:
                # No valid account data - API not connected
                output_manager.print_static(f"\n{YELLOW}Account not connected{RESET}\n")
        else:
            # Exchange client is None - API not connected
            output_manager.print_static(f"\n{YELLOW}Account not connected{RESET}\n")
    except NameError as e:
        # Handle NameError for color constants
        try:
            from core import RESET, YELLOW, GREEN, WHITE, output_manager
            output_manager.print_static(f"\n{YELLOW}Account not connected{RESET}\n")
        except Exception:
            # Fallback if import fails
            try:
                from core import output_manager
                output_manager.print_static(f"\nAccount not connected\n")
            except:
                print("\nAccount not connected\n")
    except Exception as e:
        # Log error for debugging (but don't break the flow)
        # Only print if it's not a common permission issue
        error_str = str(e).lower()
        if 'permission' not in error_str and 'read-only' not in error_str:
            # Only print unexpected errors
            try:
                # Try to use color constants if available
                try:
                    _ = RESET
                    _ = YELLOW
                    output_manager.print_static(f"{YELLOW}⚠️  Error retrieving account info: {str(e)[:50]}{RESET}\n")
                except NameError:
                    from core import YELLOW, RESET
                    output_manager.print_static(f"{YELLOW}⚠️  Error retrieving account info: {str(e)[:50]}{RESET}\n")
            except Exception:
                try:
                    from core import output_manager
                    output_manager.print_static(f"⚠️  Error retrieving account info: {str(e)[:50]}\n")
                except:
                    print(f"⚠️  Error retrieving account info: {str(e)[:50]}\n")
    
    # Get supported exchanges for display
    try:
        from exchanger import ExchangeFactory
        supported_exchanges = ExchangeFactory.get_supported_exchanges()
    except Exception:
        supported_exchanges = ['BYBIT', 'BINANCE', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO']
    
    auto_continue_env = os.getenv("AUTO_CONTINUE", "0")
    AUTO_CONTINUE = auto_continue_env == "1"
    ENV_TRADING_MODE = os.getenv("TRADING_MODE")
    ENV_EXIT_STRATEGY = os.getenv("EXIT_STRATEGY")
    if DISABLE_SSL_VERIFICATION:
        output_manager.print_static(f"{RED}⚠️  WARNING: SSL VERIFICATION IS DISABLED - SECURITY RISK!{RESET}")
        output_manager.print_static(f"{YELLOW}   Set DISABLE_SSL_VERIFICATION = False for production use{RESET}")
        output_manager.print_static(f"{YELLOW}   Current mode: DEVELOPMENT (SSL verification bypassed){RESET}\n")
    optimizer_task = None
    flush_task = None
    try:
        test_result = await fast_startup_test()
        if not test_result:
            output_manager.print_static(f"{RED}❌ System tests failed. Please check your setup.{RESET}")
            if not AUTO_CONTINUE:
                confirm = input(f"{YELLOW}Continue anyway? (y/N): {RESET}").strip().lower()
                if confirm != 'y':
                    return
            else:
                output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding despite test failure{RESET}")
        optimizer_task = asyncio.create_task(real_time_performance_optimizer())
        try:
            flush_task = asyncio.create_task(periodic_candle_cache_flush())
        except Exception:
            flush_task = None
        output_manager.print_static(f"{WHITE}Trading mode selection{RESET}")
        output_manager.print_static(f"{WHITE}1. Manual Input - Choose specific coin (existing behavior){RESET}")
        output_manager.print_static(f"{WHITE}2. Auto Rotation - Auto-scan coinlist.txt & rotate coins{RESET}")
        output_manager.print_static(f"{WHITE}3. Bot Grid Spot - Grid trading bot for spot market{RESET}")
        output_manager.print_static(f"{WHITE}4. Close Orders - Cancel all pending orders in spot trading{RESET}")
        
        # Loop until valid trading mode is selected
        trading_mode = None
        if ENV_TRADING_MODE in ["1", "2", "3", "4"]:
            trading_mode = ENV_TRADING_MODE
            output_manager.print_static(f"{WHITE}Env TRADING_MODE={trading_mode} (non-interactive){RESET}")
        else:
            while True:
                trading_mode = input(f"{WHITE}select option (1-4) : {RESET}").strip()
                if trading_mode in ["1", "2", "3", "4"]:
                    break
                else:
                    output_manager.print_static(f"{RED}❌ Invalid mode selected. Please choose 1-4.{RESET}")
                    continue
        
        if trading_mode == "1":
            output_manager.print_static(f"{WHITE}Selected Mode: Manual Input{RESET}\n")
            # Outer loop to handle coin input and trading mode selection
            # This allows returning to coin input if SELL mode fails
            while True:
                # Loop until valid coin is entered
                symbol_validated = False  # Initialize flag before loop - MUST be True to exit loop
                exchange_client = None  # Initialize exchange_client before loop
                symbol = None  # Initialize symbol before loop
                while True:  # Infinite loop - will break only when symbol is validated
                    symbol_validated = False  # Initialize in loop scope
                    try:
                        coin = input(f"{WHITE}Enter coin name (e.g., BTC, ETH, SOL): {RESET}").upper().strip()
                        if not coin:
                            output_manager.print_static(f"{RED}Coin name cannot be empty{RESET}")
                            continue
                    except EOFError:
                        # EOF reached (e.g., from heredoc input) - exit gracefully
                        output_manager.print_static(f"\n{YELLOW}⚠️ Input ended unexpectedly{RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
                    
                    try:
                        symbol = f"{coin}_USDT"
                        
                        # Quick check if symbol is supported before continuing
                        from core import _init_exchange_client
                        exchange_client = _init_exchange_client()  # Store in outer scope
                        if not exchange_client:
                            # If can't get exchange client, cannot validate - exit
                            output_manager.print_static(f"{YELLOW}⚠️ Cannot validate symbol - exchange client not available{RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            return
                        
                        # Clear unsupported_symbols for this symbol first to allow fresh check
                        # (in case it was marked from previous attempt, we want to re-check)
                        unsupported_symbols = getattr(exchange_client, 'unsupported_symbols', {})
                        if symbol in unsupported_symbols:
                            # unsupported_symbols is a dict, not a set, so use del or pop()
                            if isinstance(unsupported_symbols, dict):
                                unsupported_symbols.pop(symbol, None)
                            elif isinstance(unsupported_symbols, set):
                                unsupported_symbols.discard(symbol)
                        
                        # Try to get price to validate symbol
                        # The exchange client will print the message ONCE if symbol is invalid
                        # IMPORTANT: get_price_rest may add symbol to unsupported_symbols during call
                        test_price = exchange_client.get_price_rest(symbol)
                        
                        # CRITICAL: Check unsupported_symbols IMMEDIATELY AFTER get_price_rest
                        # Symbol might be added to cache during API call
                        import time as time_module
                        time_module.sleep(0.1)  # Small delay to ensure unsupported_symbols is updated
                        unsupported_symbols_after_price = getattr(exchange_client, 'unsupported_symbols', {})
                        
                        # If price is valid, also check candles availability
                        # This prevents symbols that pass price check but fail candle check
                        candles_valid = True
                        if test_price is not None and test_price > 0 and symbol not in unsupported_symbols_after_price:
                            if hasattr(exchange_client, 'fetch_candles'):
                                try:
                                    test_candles = exchange_client.fetch_candles(symbol, "15m", 5)
                                    if not test_candles or len(test_candles) == 0:
                                        # Candles not available - mark as unsupported
                                        candles_valid = False
                                        if hasattr(exchange_client, '_add_unsupported_symbol'):
                                            exchange_client._add_unsupported_symbol(symbol)
                                        if hasattr(exchange_client, 'is_symbol_supported_cached'):
                                            if exchange_client.is_symbol_supported_cached(symbol):
                                                output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                                except Exception as e:
                                    # If fetch_candles fails, mark as unsupported
                                    candles_valid = False
                                    if hasattr(exchange_client, '_add_unsupported_symbol'):
                                        exchange_client._add_unsupported_symbol(symbol)
                                    # Also print error if symbol was previously considered supported
                                    if hasattr(exchange_client, 'is_symbol_supported_cached'):
                                        if exchange_client.is_symbol_supported_cached(symbol):
                                            output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                        
                        # CRITICAL: Final check after all validations
                        time_module.sleep(0.1)  # Additional delay for async updates
                        unsupported_symbols_after = getattr(exchange_client, 'unsupported_symbols', {})
                        
                        # DECISION LOGIC (in priority order):
                        # 1. If symbol is in unsupported_symbols -> INVALID, continue loop
                        # 2. If test_price is valid (> 0) -> VALID, set flag and exit loop
                        # 3. If test_price is None but not in unsupported -> UNCERTAIN, continue loop
                        
                        # CRITICAL: Check symbol validity in this exact order:
                        # 1. First check if symbol is in unsupported_symbols (most reliable)
                        # 2. Then check if we got a valid price
                        # 3. Otherwise, it's invalid or uncertain
                        
                        # CRITICAL VALIDATION ORDER:
                        # 1. Check unsupported_symbols FIRST (most reliable indicator)
                        # 2. Then check if we got a valid price AND candles are valid
                        # 3. If both pass, symbol is valid - break loop
                        
                        if symbol in unsupported_symbols_after:
                            # Symbol was marked as unsupported during API call
                            # This means symbol is definitely invalid
                            # Error message already printed by exchange client
                            symbol_validated = False
                            continue  # Loop back to input - DO NOT break or proceed
                        
                        # Check if we have valid price AND candles
                        if test_price is not None and test_price > 0 and candles_valid:
                            # All checks pass - symbol is valid!
                            symbol_validated = True
                            # Final verification: ensure symbol is still not in unsupported list
                            final_check = getattr(exchange_client, 'unsupported_symbols', {})
                            if symbol not in final_check:
                                break  # Break from loop - symbol is valid
                            else:
                                # Symbol was added to unsupported list at the last moment
                                symbol_validated = False
                                continue
                        elif test_price is None or test_price <= 0:
                            # Price is invalid or None
                            symbol_validated = False
                            output_manager.print_static(f"{YELLOW}⚠️ Cannot get price for {symbol}. Please check if symbol is correct.{RESET}")
                            continue
                        elif not candles_valid:
                            # Price is valid but candles are not available
                            symbol_validated = False
                            # Error already printed above
                            continue
                    except KeyboardInterrupt:
                        # User interrupted - exit gracefully
                        output_manager.print_static(f"\n{YELLOW}⚠️ User interrupted{RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
                    except Exception as e:
                        # Unexpected error - log and continue loop
                        output_manager.print_static(f"{RED}❌ Error validating symbol: {str(e)}{RESET}")
                        symbol_validated = False
                        continue  # Loop back to input
                
                # CRITICAL: Safety check - ensure symbol was validated before proceeding
                # This is the FINAL check before proceeding to Market Analysis
                # Loop should ONLY exit if symbol_validated is True (set at line 558)
                # If we reach here without symbol_validated=True, something went wrong
                try:
                    # Check if symbol_validated is True
                    # NOTE: symbol_validated is always defined at line 491, so we skip the locals() check
                    if not symbol_validated:
                        # Variable exists but is False - loop exited without validation
                        output_manager.print_static(f"{RED}❌ CRITICAL: Symbol validation failed{RESET}")
                        output_manager.print_static(f"{YELLOW}⚠️ Symbol validation incomplete. Please try again.{RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        continue  # Return to coin input
                except NameError:
                    # symbol_validated doesn't exist - loop exited abnormally
                    output_manager.print_static(f"{RED}❌ CRITICAL: Symbol validation variable not found{RESET}")
                    output_manager.print_static(f"{YELLOW}⚠️ Symbol validation incomplete. Please try again.{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    continue  # Return to coin input
                
                # If we reach here, symbol_validated is True and we can proceed
                # This means loop exited normally with break
                rotation_mode = False
                use_trailing_tp = True
                
                # Continue with trading logic (stay in outer loop for SELL error handling)
                # All trading logic below is within the outer loop for trading_mode == "1"
                
                # Symbol validation is already done in the input loop above for mode 1
                # For mode 2, symbol is set by scan_best_coin_from_watchlist
                # If we reach here, symbol should be valid
                
                # Import required functions for both mode 1 and mode 2
                from core import calculate_coin_volatility, get_price_optimized, analyze_hold_wait_buy_ai, TradingConfig
                import time as time_module
                
                current_utc_hour = time_module.gmtime().tm_hour
                if not (TradingConfig.TRADING_HOURS_START <= current_utc_hour < TradingConfig.TRADING_HOURS_END):
                    output_manager.print_static(f"{YELLOW}⚠️ Outside trading hours: {current_utc_hour}:00 UTC{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"Continue anyway? (y/N): ").strip().lower()
                        if confirm != 'y':
                            if optimizer_task:
                                optimizer_task.cancel()
                            continue  # Return to coin input
                    else:
                        output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding outside trading hours{RESET}")
                
                # CRITICAL: Final validation before Market Analysis - ensure symbol is valid
                # This MUST be executed BEFORE "Market Analysis" is printed
                # Re-initialize exchange_client to ensure we have the same instance (singleton pattern)
                from core import _init_exchange_client
                import time as time_module
                exchange_client_final_check = _init_exchange_client()
            
                # CRITICAL: Check unsupported_symbols BEFORE printing "Market Analysis"
                # This is the FINAL safety check - add delay to ensure cache is fully updated
                time_module.sleep(0.2)  # Delay to ensure unsupported_symbols cache is fully updated
            
                # CRITICAL: Check multiple times to ensure we catch the symbol if it was added
                for check_attempt in range(3):
                    if exchange_client_final_check:
                        # Check unsupported_symbols cache - this is the most reliable check
                        # DO NOT call get_price_rest() again - it will print duplicate error message
                        # Get fresh reference to ensure we have latest state
                        unsupported_symbols_final = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                        if symbol in unsupported_symbols_final:
                            # Symbol is in unsupported cache - DO NOT proceed to Market Analysis
                            # Error message already printed by exchange client in input loop, just exit gracefully
                            output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                            output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            continue  # Return to coin input
                    time_module.sleep(0.1)  # Small delay between checks
            
            # CRITICAL: Only proceed to Market Analysis if ALL checks pass:
            # 1. Loop exited with symbol_validated = True (line 573)
            # 2. Safety check passed (line 596-630)
            # 3. Final validation passed (line 820 - symbol NOT in unsupported_symbols_final after 3 checks)
            # If we reach here, symbol is valid and we can proceed
            
            # Only print "Market Analysis" if all checks pass
                # CRITICAL: One final check before calling calculate_coin_volatility
                # This function might call API and add symbol to unsupported_symbols
                if exchange_client_final_check:
                    final_pre_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                    if symbol in final_pre_volatility_check:
                        # Symbol was added to unsupported list - abort immediately
                        output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        continue  # Return to coin input
            
                output_manager.print_static(f"{WHITE}Market Analysis{RESET}")
                
                # CRITICAL: Check again after printing "Market Analysis" but before calling calculate_coin_volatility
                # This function might call API and add symbol to unsupported_symbols
                if exchange_client_final_check:
                    pre_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                    if symbol in pre_volatility_check:
                        # Symbol was added to unsupported list - abort immediately
                        output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        continue  # Return to coin input
            
                # CRITICAL: Check unsupported_symbols AFTER calling calculate_coin_volatility
                # This function might call fetch_candles which adds symbol to unsupported_symbols
                initial_volatility = await calculate_coin_volatility(symbol)
                
                # Check again after calculate_coin_volatility - it might have added symbol to unsupported_symbols
                if exchange_client_final_check:
                    post_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                    if symbol in post_volatility_check:
                        # Symbol was added to unsupported list during calculate_coin_volatility
                        output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        continue  # Return to coin input
            
                if initial_volatility > TradingConfig.MAX_VOLATILITY:
                    output_manager.print_static(f"{RED}🚨 VOLATILITY TOO HIGH: {initial_volatility:.1f}% > {TradingConfig.MAX_VOLATILITY}%{RESET}")
                    output_manager.print_static(f"{RED}🛑 REFUSING to trade for safety!{RESET}")
                    output_manager.print_static(f"{YELLOW}💡 Market too risky - consider lower volatility coins (BTC, ETH, SOL){RESET}")
                    output_manager.print_static(f"{CYAN}📊 Safe volatility range: {TradingConfig.MIN_VOLATILITY}% - {TradingConfig.MAX_VOLATILITY}%{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    continue  # Return to coin input
                elif initial_volatility < TradingConfig.MIN_VOLATILITY:
                    output_manager.print_static(f"{YELLOW}⚠️ Volatility too low: {initial_volatility:.1f}% < {TradingConfig.MIN_VOLATILITY}%{RESET}")
                    output_manager.print_static(f"{YELLOW}💡 Low volatility may limit profit opportunities{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"Continue with low volatility? (y/N): ").strip().lower()
                        if confirm != 'y':
                            if optimizer_task:
                                optimizer_task.cancel()
                            continue  # Return to coin input
                    else:
                        output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding with low volatility{RESET}")
                # Get exchange client for final validation
                from core import _init_exchange_client
                exchange_client = _init_exchange_client()
            
                # Double-check symbol support after volatility calculation
                # This should not happen if validation in loop worked, but just in case
                unsupported_symbols_check = getattr(exchange_client, 'unsupported_symbols', {}) if exchange_client else {}
                if exchange_client and symbol in unsupported_symbols_check:
                    output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    continue  # Return to coin input
            
                # analysis and current_price might already be set for mode 2
                if 'analysis' not in locals() or analysis is None:
                    analysis = await analyze_hold_wait_buy_ai(symbol)
                if 'current_price' not in locals() or current_price is None:
                    current_price = await get_price_optimized(symbol)
                if current_price is None:
                    # Check if symbol was marked as unsupported during price fetch
                    unsupported_symbols_check = getattr(exchange_client, 'unsupported_symbols', {}) if exchange_client else {}
                    if exchange_client and symbol in unsupported_symbols_check:
                        # Should not happen if validation worked, but handle gracefully
                        # Message might already be shown above, but show it again for clarity
                        output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    else:
                        output_manager.print_static(f"{RED}❌ Failed to get price for {symbol}{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    continue  # Return to coin input
                daily_stats = portfolio_manager.get_daily_stats()
                output_manager.print_static(f"{WHITE}💰 Current Price: ${fmt(current_price)}{RESET}")
                output_manager.print_static(f"{WHITE}📈 Market Volatility: {initial_volatility:.1f}%{RESET}")
                output_manager.print_static(f"{WHITE}🎯 Market Regime: {analysis.market_regime.value}{RESET}")
                output_manager.print_static(f"{WHITE}⚡ AI Recommendation: {analysis.recommendation.value} ({analysis.confidence:.1f}% confidence){RESET}")
                if rotation_mode:
                    output_manager.print_static(f"\n{GREEN}🤖 AUTO ROTATION MODE - Starting immediately...{RESET}")
                    mode = "4"
                else:
                    output_manager.print_static(f"\n{WHITE}TRADING MODES{RESET}")
                    output_manager.print_static(f"{WHITE}1. Quantitative Trailing SELL (ML Enhanced){RESET}")
                    output_manager.print_static(f"{WHITE}2. Quantitative Trailing BUY (ML Enhanced){RESET}")
                    output_manager.print_static(f"{WHITE}3. Quantitative AI Analysis (ML + Pattern Recognition){RESET}")
                    output_manager.print_static(f"{WHITE}4. Auto-Trader (AI Controlled){RESET}")
                    output_manager.print_static(f"{WHITE}5. Exit{RESET}")
                
                    # Loop until valid mode is selected
                    mode = None
                    while True:
                        mode = input(f"{WHITE}Select option (1-5): {RESET}").strip()
                        if mode in ["1", "2", "3", "4", "5"]:
                            break
                        else:
                            output_manager.print_static(f"{RED}❌ Invalid option selected. Please choose 1-5.{RESET}")
                            continue

                
                # Validation for SELL mode: check if coin exists in spot balance (do this first, skip USDT check)
                if mode == "1":  # SELL mode
                    from core import _init_exchange_client
                    exchange_client = _init_exchange_client()
                    if exchange_client:
                        try:
                            account_info = exchange_client.get_account_info()
                            if account_info:
                                # Extract coin name from symbol (e.g., BTC_USDT -> BTC)
                                coin_name = symbol.split('_')[0].upper()
                                
                                spot_balances = account_info.get('spot_balances', [])
                                coin_balance = None
                                for balance in spot_balances:
                                    if balance.get('coin', '').upper() == coin_name:
                                        coin_balance = balance
                                        break
                            
                                if coin_balance is None:
                                    # Coin not found in spot balances
                                    output_manager.print_static(f"\n{RED}⚠️  COIN NOT FOUND IN SPOT BALANCE{RESET}")
                                    output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                    output_manager.print_static(f"{RED}Coin: {coin_name}{RESET}")
                                    output_manager.print_static(f"{YELLOW}You cannot SELL {coin_name} because you don't have it in your spot balance{RESET}")
                                    output_manager.print_static(f"{CYAN}💡 Available coins for SELL:{RESET}")
                                    
                                    # Show available coins for sell
                                    if spot_balances:
                                        available_coins = []
                                        for balance in spot_balances:
                                            coin = balance.get('coin', '')
                                            available = balance.get('available', 0)
                                            if available > 0.0001:  # Only show coins with sufficient balance
                                                available_coins.append((coin, available))
                                        
                                        if available_coins:
                                            # Sort by coin name
                                            available_coins.sort(key=lambda x: x[0])
                                            output_manager.print_static(f"{CYAN}   (Coins with balance > 0.0001){RESET}")
                                            for coin, bal in available_coins:
                                                output_manager.print_static(f"{WHITE}   - {coin}: {bal:.8f}{RESET}")
                                        else:
                                            output_manager.print_static(f"{YELLOW}   No coins with sufficient balance for SELL{RESET}")
                                    
                                    output_manager.print_static(f"{CYAN}💡 Please choose a coin from the list above{RESET}")
                                    output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                    output_manager.print_static(f"\n{YELLOW}Returning to coin selection...{RESET}\n")
                                    
                                    # Return to coin input loop instead of exiting
                                    # This will make the script go back to "Enter coin name" prompt
                                    # For trading_mode == "1", we're in an outer loop, so continue will restart coin input
                                    if trading_mode == "1":
                                        # Continue outer loop to restart coin input
                                        continue
                                    else:
                                        # For other modes, just return
                                        if optimizer_task:
                                            optimizer_task.cancel()
                                        if flush_task:
                                            flush_task.cancel()
                                        return
                            
                            # Check if coin has sufficient available balance
                            available_balance = coin_balance.get('available', 0)
                            wallet_balance = coin_balance.get('wallet_balance', 0)
                        
                            # Get minimum order size from exchange API
                            # Get minimum order size and minimum order value from exchange
                            min_order_size = None
                            min_order_value = None
                            
                            if hasattr(exchange_client, 'get_min_order_size'):
                                try:
                                    min_order_size = exchange_client.get_min_order_size(symbol)
                                except Exception:
                                    pass
                            
                            if hasattr(exchange_client, 'get_min_order_value'):
                                try:
                                    min_order_value = exchange_client.get_min_order_value(symbol)
                                except Exception:
                                    pass
                            
                            # Get current price to calculate minimum quantity by value
                            current_price = None
                            try:
                                current_price = exchange_client.get_price_rest(symbol)
                            except Exception:
                                pass
                            
                            # Calculate minimum required quantity
                            # Must meet both: minimum order size AND minimum order value (notional)
                            min_qty_by_value = None
                            if min_order_value and current_price and current_price > 0:
                                min_qty_by_value = min_order_value / current_price
                            
                            # Use the larger of min_order_size and min_qty_by_value
                            min_sell_amount = None
                            if min_order_size and min_qty_by_value:
                                min_sell_amount = max(min_order_size, min_qty_by_value)
                            elif min_order_size:
                                min_sell_amount = min_order_size
                            elif min_qty_by_value:
                                min_sell_amount = min_qty_by_value
                            
                            # Fallback to default if API doesn't return min order size
                            if min_sell_amount is None or min_sell_amount <= 0:
                                # Default minimum based on coin type (common defaults for major exchanges)
                                # For most altcoins, minimum is usually 1-10, for BTC/ETH it's lower
                                if coin_name in ['BTC', 'ETH']:
                                    min_sell_amount = 0.0001
                                elif coin_name in ['USDT', 'USDC', 'BUSD']:
                                    min_sell_amount = 1.0
                                else:
                                    # For altcoins, default to 1 (can be adjusted based on exchange)
                                    min_sell_amount = 1.0
                            
                            if available_balance < min_sell_amount:
                                output_manager.print_static(f"\n{RED}⚠️  INSUFFICIENT COIN BALANCE FOR SELL{RESET}")
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                output_manager.print_static(f"{YELLOW}Coin: {coin_name}{RESET}")
                                output_manager.print_static(f"{YELLOW}Available Balance: {available_balance:.8f} {coin_name}{RESET}")
                                output_manager.print_static(f"{YELLOW}Total Balance: {wallet_balance:.8f} {coin_name}{RESET}")
                                if wallet_balance > available_balance:
                                    locked = wallet_balance - available_balance
                                    output_manager.print_static(f"{YELLOW}Locked Balance: {locked:.8f} {coin_name} (pending orders/deposits/withdrawals){RESET}")
                                # Show detailed minimum requirement
                                min_details = []
                                if min_order_size:
                                    min_details.append(f"min size: {min_order_size:.8f}")
                                if min_order_value and min_qty_by_value:
                                    min_details.append(f"min value: ${min_order_value:.2f} ({min_qty_by_value:.8f} @ ${current_price:.4f})")
                                
                                detail_str = f" ({', '.join(min_details)})" if min_details else ""
                                output_manager.print_static(f"{YELLOW}Minimum Required: {min_sell_amount:.8f} {coin_name}{detail_str}{RESET}")
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                output_manager.print_static(f"{YELLOW}⚠️  You need at least {min_sell_amount:.8f} {coin_name} to execute SELL orders (from {core.SELECTED_EXCHANGE}){RESET}")
                                output_manager.print_static(f"\n{YELLOW}Returning to coin selection...{RESET}\n")
                                
                                # Return to coin input loop instead of exiting
                                if trading_mode == "1":
                                    # Continue outer loop to restart coin input
                                    continue
                                else:
                                    # For other modes, just return
                                    if optimizer_task:
                                        optimizer_task.cancel()
                                    if flush_task:
                                        flush_task.cancel()
                                    return
                            else:
                                # Show minimum order size info
                                min_info = f" (min: {min_sell_amount:.8f} from {core.SELECTED_EXCHANGE})" if min_sell_amount else ""
                                output_manager.print_static(f"{GREEN}✅ Coin Balance Check: {available_balance:.8f} {coin_name} available for SELL{min_info}{RESET}")
                            
                            # Store coin balance info for later use in enhanced_manual_trailing
                            # We'll pass this through a global or modify the function signature
                            # For now, store in a way that enhanced_manual_trailing can access
                            try:
                                import core
                                if not hasattr(core, '_sell_balance_info'):
                                    core._sell_balance_info = {}
                                core._sell_balance_info[symbol] = {
                                    'coin_name': coin_name,
                                    'available_balance': available_balance,
                                    'min_order_size': min_order_size if min_order_size else min_sell_amount,
                                    'min_order_value': min_order_value,
                                    'min_qty_by_value': min_qty_by_value
                                }
                            except Exception as e:
                                # Error storing balance info - warn but allow to continue
                                pass
                        except Exception as e:
                            # Error checking coin balance - warn but allow to continue
                            output_manager.print_static(f"{YELLOW}⚠️  Coin balance check failed: {str(e)}{RESET}")
                            if not AUTO_CONTINUE:
                                confirm = input(f"{YELLOW}Continue without coin balance verification? (y/N): {RESET}").strip().lower()
                                if confirm != 'y':
                                    output_manager.print_static(f"{YELLOW}SELL cancelled{RESET}")
                                    if optimizer_task:
                                        optimizer_task.cancel()
                                    if flush_task:
                                        flush_task.cancel()
                                    continue  # Return to coin input (inside while True loop)
                
                # Trading hours check and final validation (for ALL modes, inside loop)
                # This applies to both SELL (mode 1) and BUY (mode 2) modes
                from core import calculate_coin_volatility, get_price_optimized, analyze_hold_wait_buy_ai, TradingConfig
                import time as time_module
                
                current_utc_hour = time_module.gmtime().tm_hour
                if not (TradingConfig.TRADING_HOURS_START <= current_utc_hour < TradingConfig.TRADING_HOURS_END):
                    output_manager.print_static(f"{YELLOW}⚠️ Outside trading hours: {current_utc_hour}:00 UTC{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"Continue anyway? (y/N): ").strip().lower()
                        if confirm != 'y':
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            continue  # Return to coin input (inside while True loop)
                    else:
                        output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding outside trading hours{RESET}")
                
                # CRITICAL: Final validation before Market Analysis - ensure symbol is valid
                from core import _init_exchange_client
                import time as time_module
                exchange_client_final_check = _init_exchange_client()
                time_module.sleep(0.2)  # Delay to ensure unsupported_symbols cache is fully updated
                
                # CRITICAL: Check multiple times to ensure we catch the symbol if it was added
                for check_attempt in range(3):
                    if exchange_client_final_check:
                        unsupported_symbols_final = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                        if symbol in unsupported_symbols_final:
                            output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                            output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            continue  # Return to coin input (inside while True loop)
                    time_module.sleep(0.1)  # Small delay between checks
                
                # Break from outer loop only when trading is successful or user exits
                # This applies to ALL modes (1, 2, 3, 4, 5)
                break  # Exit outer loop after successful trading setup
        elif trading_mode == "2":
            output_manager.print_static(f"{GREEN}Selected Mode: Auto Rotation{RESET}")
            output_manager.print_static(f"{WHITE}Bot will scan coinlist.txt and auto-trade best coins{RESET}\n")
            
            # Set rotation_mode for mode 2 (Auto Rotation)
            rotation_mode = True
            
            # Show trading mode selection for Auto Rotation BEFORE scanning coins
            output_manager.print_static(f"{WHITE}Select trading mode:{RESET}")
            output_manager.print_static(f"{WHITE}1. Fixed Mode (Fixed Stop Loss & Take Profit){RESET}")
            output_manager.print_static(f"{WHITE}2. Trailing Mode (Trailing Stop Loss & Take Profit){RESET}")
            
            # Get user selection
            auto_rotation_mode = None
            if AUTO_CONTINUE:
                # Default to Trailing Mode for non-interactive mode
                auto_rotation_mode = "2"
                output_manager.print_static(f"{CYAN}AUTO_CONTINUE enabled - Auto-selecting Trailing Mode{RESET}")
            else:
                while True:
                    try:
                        auto_rotation_mode = input(f"{WHITE}select option (1-2) : {RESET}").strip()
                        if not auto_rotation_mode:
                            auto_rotation_mode = "2"  # Default to Trailing Mode
                        if auto_rotation_mode in ["1", "2"]:
                            break
                        else:
                            output_manager.print_static(f"{RED}❌ Invalid option selected. Please choose 1-2.{RESET}")
                            continue
                    except (EOFError, KeyboardInterrupt):
                        output_manager.print_static(f"\n{YELLOW}⚠️ Input cancelled. Exiting...{RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
            
            # Set use_trailing_tp based on selection
            if auto_rotation_mode == "1":
                use_trailing_tp = False
                output_manager.print_static(f"{GREEN}Selected Trading Mode: Fixed Mode{RESET}\n")
            else:  # auto_rotation_mode == "2"
                use_trailing_tp = True
                output_manager.print_static(f"{GREEN}Selected Trading Mode: Trailing Mode{RESET}")
            
            # Check USDT balance and get trading amount BEFORE scanning coins
            trading_amount = None
            available_balance = 0
            min_trading_balance = 5.0
            
            if trading_mode == "2":
                # Validate USDT balance before scanning
                from core import _init_exchange_client
                exchange_client = _init_exchange_client()
                if exchange_client:
                    try:
                        account_info = exchange_client.get_account_info()
                        if account_info:
                            usdt_balance = account_info.get('usdt_balance', 0)
                            available_balance = usdt_balance
                            
                            # Check spot balances for USDT
                            spot_balances = account_info.get('spot_balances', [])
                            for balance in spot_balances:
                                if balance.get('coin') == 'USDT':
                                    available_balance = balance.get('available', 0)
                                    break
                            
                            # Minimum balance for trading - get from exchange API or use default
                            min_trading_balance = 5.0  # Default minimum $5 USDT (most exchanges)
                            
                            # Try to get actual minimum order value from exchange
                            try:
                                # Use a common trading pair to get minimum order value
                                test_symbol = "BTC_USDT"  # Most exchanges have BTC/USDT
                                if hasattr(exchange_client, 'get_min_order_value'):
                                    min_order_value = exchange_client.get_min_order_value(test_symbol)
                                    if min_order_value and min_order_value > 0:
                                        min_trading_balance = min_order_value
                            except Exception:
                                pass  # Use default if API call fails
                            
                            if available_balance < min_trading_balance:
                                output_manager.print_static(f"\n{RED}⚠️  INSUFFICIENT BALANCE FOR TRADING{RESET}")
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                if available_balance > 0:
                                    output_manager.print_static(f"{YELLOW}Current USDT Balance: ${available_balance:.2f}{RESET}")
                                    output_manager.print_static(f"{YELLOW}Minimum Required: ${min_trading_balance:.2f}{RESET}")
                                    output_manager.print_static(f"{YELLOW}Shortage: ${min_trading_balance - available_balance:.2f}{RESET}")
                                else:
                                    output_manager.print_static(f"{RED}No USDT balance available{RESET}")
                                    output_manager.print_static(f"{YELLOW}Please deposit USDT to your {SELECTED_EXCHANGE} account{RESET}")
                                
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                output_manager.print_static(f"{YELLOW}⚠️  Trading requires sufficient balance to execute orders{RESET}")
                                
                                if not AUTO_CONTINUE:
                                    confirm = input(f"\n{YELLOW}Continue anyway? (y/N): {RESET}").strip().lower()
                                    if confirm != 'y':
                                        output_manager.print_static(f"{YELLOW}Trading cancelled due to insufficient balance{RESET}")
                                        if optimizer_task:
                                            optimizer_task.cancel()
                                        if flush_task:
                                            flush_task.cancel()
                                        return
                                    else:
                                        output_manager.print_static(f"{YELLOW}⚠️  Proceeding with insufficient balance - trades may fail{RESET}")
                                else:
                                    output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding despite insufficient balance{RESET}")
                            else:
                                # Ask user for trading amount
                                output_manager.print_static(f"\n{WHITE}💰 Trading Amount Configuration{RESET}")
                                output_manager.print_static(f"{WHITE}Available Balance: ${available_balance:.2f} USDT on spot account{RESET}")
                                output_manager.print_static(f"{WHITE}Minimum Order Value: ${min_trading_balance:.2f} USDT{RESET}")
                                
                                if AUTO_CONTINUE:
                                    # Auto mode: use $5.00 default
                                    trading_amount = 5.00
                                    trading_amount = max(trading_amount, min_trading_balance)
                                    trading_amount = min(trading_amount, available_balance)
                                    output_manager.print_static(f"{CYAN}AUTO_CONTINUE enabled - Using ${trading_amount:.2f} USDT{RESET}")
                                    output_manager.print_static(f"{GREEN}✅ Trading amount confirmed: ${trading_amount:.2f} USDT{RESET}")
                                else:
                                    while True:
                                        try:
                                            amount_input = input(f"{YELLOW}Enter USDT amount to use for trading (min: $5.00, max: ${available_balance:.2f}): {RESET}").strip()
                                            
                                            if not amount_input:
                                                output_manager.print_static(f"{RED}Amount is required. Please enter a value.{RESET}")
                                                continue
                                            
                                            trading_amount = float(amount_input)
                                            
                                            # Validate amount
                                            if trading_amount <= 0:
                                                output_manager.print_static(f"{RED}Amount must be greater than 0.{RESET}")
                                                continue
                                            
                                            # Ensure minimum is at least 5 USDT
                                            min_required = max(min_trading_balance, 5.00)
                                            if trading_amount < min_required:
                                                output_manager.print_static(f"{RED}Amount ${trading_amount:.2f} is less than minimum required ${min_required:.2f} USDT{RESET}")
                                                continue
                                            
                                            if trading_amount > available_balance:
                                                output_manager.print_static(f"{RED}Amount ${trading_amount:.2f} exceeds available balance ${available_balance:.2f} USDT{RESET}")
                                                continue
                                            
                                            # Valid amount
                                            output_manager.print_static(f"{GREEN}✅ Trading amount confirmed: ${trading_amount:.2f} USDT{RESET}")
                                            break
                                        except ValueError:
                                            output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")
                                            continue
                                        except (EOFError, KeyboardInterrupt):
                                            output_manager.print_static(f"\n{YELLOW}⚠️ Input cancelled. Exiting...{RESET}")
                                            if optimizer_task:
                                                optimizer_task.cancel()
                                            if flush_task:
                                                flush_task.cancel()
                                            return
                                
                                # Store trading amount for Auto Rotation
                                try:
                                    import core
                                    if not hasattr(core, '_buy_balance_info'):
                                        core._buy_balance_info = {}
                                    core._buy_balance_info['available_usdt'] = available_balance
                                    core._buy_balance_info['min_trading_balance'] = min_trading_balance
                                    core._buy_balance_info['trading_amount_rotation'] = trading_amount
                                except Exception:
                                    pass
                                
                                # Display bot summary BEFORE scanning coins
                                output_manager.print_static(f"\n{MAGENTA}🤖 BOT CONFIGURATION SUMMARY{RESET}")
                                
                                # Exchange and Mode
                                output_manager.print_static(f"{WHITE}📊 Exchange: {SELECTED_EXCHANGE}{RESET}")
                                output_manager.print_static(f"{WHITE}🎯 Trading Mode: Auto Rotation{RESET}")
                                
                                # Trading Strategy
                                strategy_name = "Trailing Mode (Trailing Stop Loss & Take Profit)" if use_trailing_tp else "Fixed Mode (Fixed Stop Loss & Take Profit)"
                                output_manager.print_static(f"{WHITE}⚙️  Strategy: {strategy_name}{RESET}")
                                
                                # Trading Amount and Balance
                                output_manager.print_static(f"{WHITE}💸 Trading Amount: ${trading_amount:.2f} USDT{RESET}")
                                output_manager.print_static(f"{WHITE}💼 Available Balance: ${available_balance:.2f} USDT on spot account{RESET}")
                                
                                # Note about coin selection
                                output_manager.print_static(f"{CYAN}📋 Coin Selection: Will scan coinlist.txt for best coin{RESET}")
                                
                                # Ask for confirmation
                                if AUTO_CONTINUE:
                                    output_manager.print_static(f"{CYAN}AUTO_CONTINUE enabled - Proceeding automatically...{RESET}\n")
                                else:
                                    while True:
                                        try:
                                            confirm = input(f"{YELLOW}Proceed with this configuration? (Y/N): {RESET}").strip().lower()
                                            if confirm == 'y':
                                                output_manager.print_static(f"{GREEN}✅ Starting bot...{RESET}")
                                                # Countdown 3 seconds
                                                import time
                                                for i in range(3, 0, -1):
                                                    output_manager.print_static(f"{YELLOW}Starting in {i}...{RESET}")
                                                    time.sleep(1)
                                                # Clear screen DISABLED - keep summary visible for user reference
                                                # os.system('clear' if os.name != 'nt' else 'cls')
                                                output_manager.print_static(f"{CYAN}{'='*80}{RESET}")
                                                output_manager.print_static(f"{GREEN}🚀 Bot started - Summary above for reference{RESET}")
                                                output_manager.print_static(f"{CYAN}{'='*80}{RESET}\n")
                                                break
                                            elif confirm == 'n' or confirm == '':
                                                output_manager.print_static(f"{YELLOW}Trading cancelled{RESET}")
                                                if optimizer_task:
                                                    optimizer_task.cancel()
                                                if flush_task:
                                                    flush_task.cancel()
                                                return
                                            else:
                                                output_manager.print_static(f"{RED}Please enter 'y' to continue or 'n' to cancel{RESET}")
                                        except (EOFError, KeyboardInterrupt):
                                            output_manager.print_static(f"\n{YELLOW}⚠️ Input cancelled. Exiting...{RESET}")
                                            if optimizer_task:
                                                optimizer_task.cancel()
                                            if flush_task:
                                                flush_task.cancel()
                                            return
                    except Exception as e:
                        # Error checking balance - warn but allow to continue
                        output_manager.print_static(f"{YELLOW}⚠️  Balance check failed: {str(e)}{RESET}")
                        if not AUTO_CONTINUE:
                            confirm = input(f"{YELLOW}Continue without balance verification? (y/N): {RESET}").strip().lower()
                            if confirm != 'y':
                                output_manager.print_static(f"{YELLOW}Trading cancelled{RESET}")
                                if optimizer_task:
                                    optimizer_task.cancel()
                                if flush_task:
                                    flush_task.cancel()
                                return
                else:
                    # Exchange client not available
                    output_manager.print_static(f"{YELLOW}⚠️  Cannot verify balance - exchange client unavailable{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"{YELLOW}Continue without balance verification? (y/N): {RESET}").strip().lower()
                        if confirm != 'y':
                            output_manager.print_static(f"{YELLOW}Trading cancelled{RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            return
        elif trading_mode == "3":
            output_manager.print_static(f"{WHITE}Selected Mode: Bot Grid Spot{RESET}\n")
            output_manager.print_static(f"{CYAN}🤖 BOT GRID SPOT MODE{RESET}\n")
            
            # Grid trading bot implementation
            from core import _init_exchange_client, get_price_optimized, load_config
            from core import portfolio_manager, output_manager as core_output_manager
            
            # Get coin selection
            coin = None
            if AUTO_CONTINUE:
                # Auto mode: use first coin from coinlist or default
                try:
                    watchlist_path = os.path.join(os.path.dirname(__file__), 'coinlist.txt')
                    if os.path.exists(watchlist_path):
                        with open(watchlist_path, 'r') as f:
                            watchlist = [line.strip().upper() for line in f if line.strip() and not line.strip().startswith('#')]
                        if watchlist:
                            coin = watchlist[0]
                except Exception:
                    pass
                if not coin:
                    coin = "BTC"  # Default
                output_manager.print_static(f"{CYAN}AUTO_CONTINUE enabled - Using coin: {coin}{RESET}")
            else:
                while True:
                    try:
                        coin = input(f"{WHITE}Enter coin name for grid trading (e.g., BTC, ETH, SOL): {RESET}").upper().strip()
                        if coin:
                            break
                        else:
                            output_manager.print_static(f"{RED}Coin name cannot be empty{RESET}")
                    except (EOFError, KeyboardInterrupt):
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
            
            symbol = f"{coin}_USDT"
            
            # Get current price
            exchange_client = _init_exchange_client()
            if not exchange_client:
                output_manager.print_static(f"{RED}❌ Failed to initialize exchange client{RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                return
            
            try:
                current_price = await get_price_optimized(symbol)
                if not current_price:
                    output_manager.print_static(f"{RED}❌ Failed to get price for {symbol}{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    return
                
                output_manager.print_static(f"{GREEN}✅ Current {symbol} Price: ${current_price:.8f}{RESET}\n")
                
                # Get balance
                account_info = exchange_client.get_account_info()
                available_balance = 0.0
                if account_info:
                    # Use spot_balances (same structure as used in account display)
                    spot_balances = account_info.get('spot_balances', [])
                    for bal in spot_balances:
                        if bal.get('coin') == 'USDT':
                            available_balance = float(bal.get('available', 0))
                            break
                
                if available_balance < 5.0:
                    output_manager.print_static(f"{RED}❌ Insufficient USDT balance. Minimum: $5.00, Available: ${available_balance:.2f}{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    return
                
                # Grid parameters input
                output_manager.print_static(f"{CYAN}📊 Grid Trading Configuration{RESET}")
                output_manager.print_static(f"{CYAN}Available Balance: ${available_balance:.2f} USDT on spot account{RESET}")
                output_manager.print_static(f"{CYAN}Current Price: ${current_price:.8f}{RESET}\n")
                
                # Grid parameters
                if AUTO_CONTINUE:
                    grid_spread_pct = 5.0  # Default 5% spread
                    grid_count = 5  # Default 5 grids
                    use_amount = available_balance * 0.8  # Use 80% of balance
                    output_manager.print_static(f"{CYAN}AUTO_CONTINUE: Using default grid parameters{RESET}")
                else:
                    try:
                        spread_input = input(f"{YELLOW}Enter grid spread percentage (default 5.0%): {RESET}").strip()
                        grid_spread_pct = float(spread_input) if spread_input else 5.0
                        
                        count_input = input(f"{YELLOW}Enter number of grids (default 5): {RESET}").strip()
                        grid_count = int(count_input) if count_input else 5
                        
                        amount_input = input(f"{YELLOW}Enter USDT amount to use (max ${available_balance:.2f}, default: ${available_balance * 0.8:.2f}): {RESET}").strip()
                        use_amount = float(amount_input) if amount_input else (available_balance * 0.8)
                    except (EOFError, KeyboardInterrupt, ValueError):
                        output_manager.print_static(f"{YELLOW}Using default parameters{RESET}")
                        grid_spread_pct = 5.0
                        grid_count = 5
                        use_amount = available_balance * 0.8
                
                use_amount = min(use_amount, available_balance)
                use_amount = max(use_amount, 5.0)  # Minimum $5
                
                # Calculate grid levels
                upper_price = current_price * (1 + grid_spread_pct / 100.0)
                lower_price = current_price * (1 - grid_spread_pct / 100.0)
                price_range = upper_price - lower_price
                grid_spacing = price_range / grid_count
                
                output_manager.print_static(f"\n{CYAN}📊 Grid Setup:{RESET}")
                output_manager.print_static(f"{CYAN}  Upper Price: ${upper_price:.8f} (+{grid_spread_pct:.1f}%){RESET}")
                output_manager.print_static(f"{CYAN}  Lower Price: ${lower_price:.8f} (-{grid_spread_pct:.1f}%){RESET}")
                output_manager.print_static(f"{CYAN}  Grid Count: {grid_count}{RESET}")
                output_manager.print_static(f"{CYAN}  Grid Spacing: ${grid_spacing:.8f}{RESET}")
                output_manager.print_static(f"{CYAN}  Capital Used: ${use_amount:.2f} USDT{RESET}")
                output_manager.print_static(f"{CYAN}  Order Size per Grid: ${use_amount / grid_count:.2f} USDT{RESET}\n")
                
                # Start grid trading bot
                output_manager.print_static(f"{GREEN}🚀 Starting Grid Trading Bot...{RESET}\n")
                from core import start_grid_trading_bot
                await start_grid_trading_bot(symbol, lower_price, upper_price, grid_count, use_amount, current_price)
                
            except Exception as e:
                output_manager.print_static(f"{RED}❌ Grid trading error: {e}{RESET}")
                import traceback
                traceback.print_exc()
            
            # Cleanup
            if optimizer_task:
                optimizer_task.cancel()
            if flush_task:
                flush_task.cancel()
            return
            
        elif trading_mode == "4":
            output_manager.print_static(f"{WHITE}Selected Mode: Close Orders{RESET}\n")
            # Cancel all active trading strategies/tasks first
            try:
                from core import task_manager
                if hasattr(task_manager, 'cancel_all_tasks'):
                    if asyncio.iscoroutinefunction(task_manager.cancel_all_tasks):
                        await task_manager.cancel_all_tasks("ALL")
                    else:
                        task_manager.cancel_all_tasks("ALL")
            except Exception:
                pass
            
            # Close all pending orders in spot trading
            # This will print detailed message about cancelled orders
            from core import _init_exchange_client
            exchange_client = _init_exchange_client()
            if exchange_client:
                try:
                    if hasattr(exchange_client, 'cancel_all_spot_orders'):
                        exchange_client.cancel_all_spot_orders()
                except Exception:
                    pass
            
            # Stop WebSocket streams (silently)
            try:
                from core import _init_ws_manager
                ws_manager = _init_ws_manager()
                if ws_manager:
                    await ws_manager.stop_all_streams()
            except Exception:
                pass
            
            # Print final message after all operations
            output_manager.print_static(f"{GREEN}All trading strategies stopped{RESET}")
            
            if optimizer_task:
                optimizer_task.cancel()
            if flush_task:
                flush_task.cancel()
            
            # Exit immediately without cleanup output
            sys.exit(0)
        else:
            # This should not happen since we validate above, but keep as safety check
            output_manager.print_static(f"{RED}Invalid mode selected. Please choose 1-4.{RESET}")
            if optimizer_task:
                optimizer_task.cancel()
            if flush_task:
                flush_task.cancel()
            return
        
        # Continue with trading mode 1 or 2 only
        # Note: trading_mode == "3" (Bot Grid Spot) and "4" (Close Orders) already returned above
        # Note: trading_mode == "1" already handled coin input above, symbol is already set
        # trading_mode == "2" needs to scan watchlist and select symbol (AUTO SCAN MODE)
        # Note: Mode selection (Fixed/Trailing) is already done above for mode 2
        if trading_mode == "2":
            # Auto scan best coin from watchlist (after mode selection)
            output_manager.print_static(f"\n{MAGENTA}🤖 AUTO SCAN + BUY MODE (Mode 2){RESET}")
            output_manager.print_static(f"{CYAN}Scanning for best coin from watchlist...{RESET}")
            
            # Load watchlist
            watchlist = []
            try:
                watchlist_path = os.path.join(os.path.dirname(__file__), 'coinlist.txt')
                if os.path.exists(watchlist_path):
                    with open(watchlist_path, 'r') as f:
                        watchlist = [line.strip().upper() for line in f if line.strip() and not line.strip().startswith('#')]
                if not watchlist:
                    # Fallback to default
                    watchlist = ['BTC', 'ETH', 'SOL', 'BNB', 'ADA']
            except Exception:
                watchlist = ['BTC', 'ETH', 'SOL', 'BNB', 'ADA']
            
            if not watchlist:
                output_manager.print_static(f"{RED}❌ No coins in watchlist. Please add coins to coinlist.txt{RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                return
            
            # Scan best coin
            # Initialize rotation_analysis in outer scope to ensure availability in rotation loop
            rotation_analysis = None
            from core import scan_best_coin_from_watchlist
            try:
                # No confidence filter - let bot pick best coin from scan, filter in pre-buy validation
                symbol, analysis, all_results = await scan_best_coin_from_watchlist(watchlist, exclude_cooldown=True, min_confidence=0.0)
                
                if not symbol or not analysis:
                    output_manager.print_static(f"{YELLOW}⚠️  No suitable coin found in watchlist{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    return
                
                # Get current price and volatility for the selected coin
                from core import get_price_optimized, calculate_coin_volatility, fmt
                current_price = await get_price_optimized(symbol)
                if not current_price:
                    output_manager.print_static(f"{RED}❌ Cannot get price for {symbol}{RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    return
                
                initial_volatility = await calculate_coin_volatility(symbol)
                
                # CRITICAL: Store analysis from scan for use in rotation loop
                # This ensures consistency between scan and rotation (no re-analysis)
                rotation_analysis = analysis  # Store for rotation loop
                
                output_manager.print_static(f"\n{GREEN}✅ Best Coin Selected: {symbol}{RESET}")
                output_manager.print_static(f"{CYAN}💰 Current Price: ${fmt(current_price)}{RESET}")
                output_manager.print_static(f"{CYAN}📈 Market Volatility: {initial_volatility:.1f}%{RESET}")
                output_manager.print_static(f"{CYAN}🎯 Market Regime: {analysis.market_regime.value}{RESET}")
                output_manager.print_static(f"{CYAN}⚡ AI Recommendation: {analysis.recommendation.value} ({analysis.confidence:.1f}% confidence){RESET}")
                
                # Display Futures Market Signals after Market Analysis
                # all_results is the best_score dict from scan_best_coin_from_watchlist
                if all_results and isinstance(all_results, dict):
                    d = all_results.get('details', {})
                    futures_sentiment = d.get('futures_sentiment', {})
                    futures_risk = d.get('futures_risk', {})
                    futures_signal_score = d.get('futures_signal_score', 0)
                    
                    if futures_sentiment or futures_risk:
                        from core import GREEN, YELLOW, RED, CYAN, RESET
                        output_manager.print_static(f"\n{CYAN}📊 Futures Market Signals:{RESET}")
                        if futures_sentiment:
                            premium = futures_sentiment.get('premium')
                            premium_signal = futures_sentiment.get('premium_signal', 'neutral')
                            momentum_signal = futures_sentiment.get('momentum_signal', 'neutral')
                            price_change = futures_sentiment.get('price_change_1h')
                            
                            if premium is not None:
                                premium_color = GREEN if premium > 0.5 else YELLOW if premium > -0.5 else RED
                                output_manager.print_static(
                                    f"  {CYAN}Premium/Discount: {premium_color}{premium:+.2f}%{RESET} ({premium_signal})"
                                )
                            if price_change is not None:
                                change_color = GREEN if price_change > 1.0 else YELLOW if price_change > -1.0 else RED
                                data_source = "Futures" if futures_sentiment.get('using_futures_data', False) else "Spot (fallback)"
                                output_manager.print_static(
                                    f"  {CYAN}Price Change (1h): {change_color}{price_change:+.2f}%{RESET} ({momentum_signal}) | Source: {data_source}"
                                )
                        
                        if futures_risk:
                            risk_level = futures_risk.get('risk_level', 'low')
                            volatility_ratio = futures_risk.get('volatility_ratio', 1.0)
                            risk_color = GREEN if risk_level == 'low' else YELLOW if risk_level == 'medium' else RED
                            output_manager.print_static(
                                f"  {CYAN}Risk Level: {risk_color}{risk_level.upper()}{RESET} (Volatility Ratio: {volatility_ratio:.2f}x)"
                            )
                        
                        if futures_signal_score != 0:
                            signal_color = GREEN if futures_signal_score > 0 else RED
                            output_manager.print_static(
                                f"  {CYAN}Futures Signal Score: {signal_color}{futures_signal_score:+.1f}{RESET}"
                            )
                
                output_manager.print_static("")
                
            except Exception as e:
                output_manager.print_static(f"{RED}❌ Error scanning coins: {str(e)}{RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                return
        # trading_mode == "1" already handled above, symbol is already set
        # trading_mode == "2" needs trading hours check and final validation
        if trading_mode == "2":
            # Import required functions for mode 2
            from core import (
                calculate_coin_volatility, get_price_optimized, analyze_hold_wait_buy_ai,
                calculate_ai_recommended_delta, calculate_smart_activation_price,
                safe_division, portfolio_manager, mark_coin_skip_rotation, get_skip_rotations_remaining,
                TradingConfig
            )
            from core import ActionRecommendation
            
            import time as time_module
            current_utc_hour = time_module.gmtime().tm_hour
            if not (TradingConfig.TRADING_HOURS_START <= current_utc_hour < TradingConfig.TRADING_HOURS_END):
                output_manager.print_static(f"{YELLOW}⚠️ Outside trading hours: {current_utc_hour}:00 UTC{RESET}")
                if not AUTO_CONTINUE:
                    confirm = input(f"Continue anyway? (y/N): ").strip().lower()
                    if confirm != 'y':
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
                else:
                    output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding outside trading hours{RESET}")
            
            # CRITICAL: Final validation before Market Analysis - ensure symbol is valid
            from core import _init_exchange_client
            exchange_client_final_check = _init_exchange_client()
            time_module.sleep(0.2)  # Delay to ensure unsupported_symbols cache is fully updated
            
            # CRITICAL: Check multiple times to ensure we catch the symbol if it was added
            for check_attempt in range(3):
                if exchange_client_final_check:
                    unsupported_symbols_final = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                    if symbol in unsupported_symbols_final:
                        output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                        output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                        if optimizer_task:
                            optimizer_task.cancel()
                        if flush_task:
                            flush_task.cancel()
                        return
                time_module.sleep(0.1)  # Small delay between checks
            
            # CRITICAL: Only proceed to Market Analysis if ALL checks pass:
            # 1. Loop exited with symbol_validated = True (line 573)
            # 2. Safety check passed (line 596-630)
            # 3. Final validation passed (line 820 - symbol NOT in unsupported_symbols_final after 3 checks)
            # If we reach here, symbol is valid and we can proceed
            
            # Only print "Market Analysis" if all checks pass
            # CRITICAL: One final check before calling calculate_coin_volatility
            # This function might call API and add symbol to unsupported_symbols
            if exchange_client_final_check:
                final_pre_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                if symbol in final_pre_volatility_check:
                    # Symbol was added to unsupported list - abort immediately
                    output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    # For both modes, return to appropriate handler
                    # Mode 1: This code is outside the loop, so we can't use continue
                    # Mode 2: Return to exit
                    return
            
            output_manager.print_static(f"{WHITE}Market Analysis{RESET}")
            
            # CRITICAL: Check again after printing "Market Analysis" but before calling calculate_coin_volatility
            # This function might call API and add symbol to unsupported_symbols
            if exchange_client_final_check:
                pre_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                if symbol in pre_volatility_check:
                    # Symbol was added to unsupported list - abort immediately
                    output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    # For both modes, return to appropriate handler
                    # Mode 1: This code is outside the loop, so we can't use continue
                    # Mode 2: Return to exit
                    return
            
            # CRITICAL: Check unsupported_symbols AFTER calling calculate_coin_volatility
            # This function might call fetch_candles which adds symbol to unsupported_symbols
            initial_volatility = await calculate_coin_volatility(symbol)
            
            # Check again after calculate_coin_volatility - it might have added symbol to unsupported_symbols
            if exchange_client_final_check:
                post_volatility_check = getattr(exchange_client_final_check, 'unsupported_symbols', {})
                if symbol in post_volatility_check:
                    # Symbol was added to unsupported list during calculate_coin_volatility
                    output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client_final_check.exchange_name} Spot Market{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                    if optimizer_task:
                        optimizer_task.cancel()
                    if flush_task:
                        flush_task.cancel()
                    # For both modes, return to appropriate handler
                    # Mode 1: This code is outside the loop, so we can't use continue
                    # Mode 2: Return to exit
                    return
            
            if initial_volatility > TradingConfig.MAX_VOLATILITY:
                output_manager.print_static(f"{RED}🚨 VOLATILITY TOO HIGH: {initial_volatility:.1f}% > {TradingConfig.MAX_VOLATILITY}%{RESET}")
                output_manager.print_static(f"{RED}🛑 REFUSING to trade for safety!{RESET}")
                output_manager.print_static(f"{YELLOW}💡 Market too risky - consider lower volatility coins (BTC, ETH, SOL){RESET}")
                output_manager.print_static(f"{CYAN}📊 Safe volatility range: {TradingConfig.MIN_VOLATILITY}% - {TradingConfig.MAX_VOLATILITY}%{RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                if trading_mode == "2":
                    return
                # For both modes, return to appropriate handler
                # Mode 1: This code is outside the loop, so we can't use continue
                # Mode 2: Return to exit
                return
            elif initial_volatility < TradingConfig.MIN_VOLATILITY:
                output_manager.print_static(f"{YELLOW}⚠️ Volatility too low: {initial_volatility:.1f}% < {TradingConfig.MIN_VOLATILITY}%{RESET}")
                output_manager.print_static(f"{YELLOW}💡 Low volatility may limit profit opportunities{RESET}")
                if not AUTO_CONTINUE:
                    confirm = input(f"Continue with low volatility? (y/N): ").strip().lower()
                    if confirm != 'y':
                        if optimizer_task:
                            optimizer_task.cancel()
                        if trading_mode == "2":
                            return
                        # For both modes, return to appropriate handler
                        # Mode 1: This code is outside the loop, so we can't use continue
                        # Mode 2: Return to exit
                        return
                else:
                    output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding with low volatility{RESET}")
            # Get exchange client for final validation
            from core import _init_exchange_client
            exchange_client = _init_exchange_client()
            
            # Double-check symbol support after volatility calculation
            # This should not happen if validation in loop worked, but just in case
            unsupported_symbols_check = getattr(exchange_client, 'unsupported_symbols', {}) if exchange_client else {}
            if exchange_client and symbol in unsupported_symbols_check:
                output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                if trading_mode == "2":
                    return
                # For both modes, return to appropriate handler
                # Mode 1: This code is outside the loop, so we can't use continue
                # Mode 2: Return to exit
                return
            
            # analysis and current_price might already be set for mode 2
            if 'analysis' not in locals() or analysis is None:
                analysis = await analyze_hold_wait_buy_ai(symbol)
            if 'current_price' not in locals() or current_price is None:
                current_price = await get_price_optimized(symbol)
            if current_price is None:
                # Check if symbol was marked as unsupported during price fetch
                unsupported_symbols_check = getattr(exchange_client, 'unsupported_symbols', {}) if exchange_client else {}
                if exchange_client and symbol in unsupported_symbols_check:
                    # Should not happen if validation worked, but handle gracefully
                    # Message might already be shown above, but show it again for clarity
                    output_manager.print_static(f"{YELLOW}⚠️ {symbol} tidak tersedia di {exchange_client.exchange_name} Spot Market{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                else:
                    output_manager.print_static(f"{RED}❌ Failed to get price for {symbol}{RESET}")
                    output_manager.print_static(f"{YELLOW}Please check if symbol is correct (BTC, ETH, SOL, etc.){RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                if flush_task:
                    flush_task.cancel()
                if trading_mode == "2":
                    return
                # For both modes, return to appropriate handler
                # Mode 1: This code is outside the loop, so we can't use continue
                # Mode 2: Return to exit
                return
            daily_stats = portfolio_manager.get_daily_stats()
            output_manager.print_static(f"{WHITE}💰 Current Price: ${fmt(current_price)}{RESET}")
            output_manager.print_static(f"{WHITE}📈 Market Volatility: {initial_volatility:.1f}%{RESET}")
            output_manager.print_static(f"{WHITE}🎯 Market Regime: {analysis.market_regime.value}{RESET}")
            output_manager.print_static(f"{WHITE}⚡ AI Recommendation: {analysis.recommendation.value} ({analysis.confidence:.1f}% confidence){RESET}")
            if rotation_mode:
                # Auto Rotation mode - directly proceed to Auto-Trader (mode 4)
                output_manager.print_static(f"\n{GREEN}🤖 AUTO ROTATION MODE - Starting Auto-Trader...{RESET}")
                mode = "4"
            else:
                output_manager.print_static(f"\n{WHITE}TRADING MODES{RESET}")
                output_manager.print_static(f"{WHITE}1. Quantitative Trailing SELL (ML Enhanced){RESET}")
                output_manager.print_static(f"{WHITE}2. Quantitative Trailing BUY (ML Enhanced){RESET}")
                output_manager.print_static(f"{WHITE}3. Quantitative AI Analysis (ML + Pattern Recognition){RESET}")
                output_manager.print_static(f"{WHITE}4. Auto-Trader (AI Controlled){RESET}")
                output_manager.print_static(f"{WHITE}5. Exit{RESET}")
                
                # Loop until valid mode is selected
                mode = None
                while True:
                    mode = input(f"{WHITE}Select option (1-5): {RESET}").strip()
                    if mode in ["1", "2", "3", "4", "5"]:
                        break
                    else:
                        output_manager.print_static(f"{RED}❌ Invalid option selected. Please choose 1-5.{RESET}")
                        continue
            
            # Validation for SELL mode: check if coin exists in spot balance (do this first, skip USDT check)
            if mode == "1":  # SELL mode
                from core import _init_exchange_client
                exchange_client = _init_exchange_client()
                if exchange_client:
                    try:
                        account_info = exchange_client.get_account_info()
                        if account_info:
                            # Extract coin name from symbol (e.g., BTC_USDT -> BTC)
                            coin_name = symbol.split('_')[0].upper()
                            
                            spot_balances = account_info.get('spot_balances', [])
                            coin_balance = None
                            for balance in spot_balances:
                                if balance.get('coin', '').upper() == coin_name:
                                    coin_balance = balance
                                    break
                            
                            if coin_balance is None:
                                # Coin not found in spot balances
                                output_manager.print_static(f"\n{RED}⚠️  COIN NOT FOUND IN SPOT BALANCE{RESET}")
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                output_manager.print_static(f"{RED}Coin: {coin_name}{RESET}")
                                output_manager.print_static(f"{YELLOW}You cannot SELL {coin_name} because you don't have it in your spot balance{RESET}")
                                output_manager.print_static(f"{CYAN}💡 Available coins for SELL:{RESET}")
                                
                                # Show available coins for sell
                                if spot_balances:
                                    available_coins = []
                                    for balance in spot_balances:
                                        coin = balance.get('coin', '')
                                        available = balance.get('available', 0)
                                        if available > 0.0001:  # Only show coins with sufficient balance
                                            available_coins.append((coin, available))
                                    
                                    if available_coins:
                                        # Sort by coin name
                                        available_coins.sort(key=lambda x: x[0])
                                        output_manager.print_static(f"{CYAN}   (Coins with balance > 0.0001){RESET}")
                                        for coin, bal in available_coins:
                                            output_manager.print_static(f"{WHITE}   - {coin}: {bal:.8f}{RESET}")
                                    else:
                                        output_manager.print_static(f"{YELLOW}   No coins with sufficient balance for SELL{RESET}")
                                
                                output_manager.print_static(f"{CYAN}💡 Please choose a coin from the list above{RESET}")
                                output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                                output_manager.print_static(f"\n{YELLOW}Returning to coin selection...{RESET}\n")
                                
                                # Return to coin input loop instead of exiting
                                # This will make the script go back to "Enter coin name" prompt
                                # For trading_mode == "2", we're outside any loop, so we use return
                                # For trading_mode == "1", this code shouldn't be reached (validation is done earlier in the loop)
                                if optimizer_task:
                                    optimizer_task.cancel()
                                if flush_task:
                                    flush_task.cancel()
                                return  # Exit function for trading_mode == "2"
                            
                            # Check if coin has sufficient available balance
                            available_balance = coin_balance.get('available', 0)
                            wallet_balance = coin_balance.get('wallet_balance', 0)
                        
                        # Get minimum order size from exchange API
                        # Get minimum order size and minimum order value from exchange
                        min_order_size = None
                        min_order_value = None
                        
                        if hasattr(exchange_client, 'get_min_order_size'):
                            try:
                                min_order_size = exchange_client.get_min_order_size(symbol)
                            except Exception:
                                pass
                        
                        if hasattr(exchange_client, 'get_min_order_value'):
                            try:
                                min_order_value = exchange_client.get_min_order_value(symbol)
                            except Exception:
                                pass
                        
                        # Get current price to calculate minimum quantity by value
                        current_price = None
                        try:
                            current_price = exchange_client.get_price_rest(symbol)
                        except Exception:
                            pass
                        
                        # Calculate minimum required quantity
                        # Must meet both: minimum order size AND minimum order value (notional)
                        min_qty_by_value = None
                        if min_order_value and current_price and current_price > 0:
                            min_qty_by_value = min_order_value / current_price
                        
                        # Use the larger of min_order_size and min_qty_by_value
                        min_sell_amount = None
                        if min_order_size and min_qty_by_value:
                            min_sell_amount = max(min_order_size, min_qty_by_value)
                        elif min_order_size:
                            min_sell_amount = min_order_size
                        elif min_qty_by_value:
                            min_sell_amount = min_qty_by_value
                        
                        # Fallback to default if API doesn't return min order size
                        if min_sell_amount is None or min_sell_amount <= 0:
                            # Default minimum based on coin type (common defaults for major exchanges)
                            # For most altcoins, minimum is usually 1-10, for BTC/ETH it's lower
                            if coin_name in ['BTC', 'ETH']:
                                min_sell_amount = 0.0001
                            elif coin_name in ['USDT', 'USDC', 'BUSD']:
                                min_sell_amount = 1.0
                            else:
                                # For altcoins, default to 1 (can be adjusted based on exchange)
                                min_sell_amount = 1.0
                        
                        if available_balance < min_sell_amount:
                            output_manager.print_static(f"\n{RED}⚠️  INSUFFICIENT COIN BALANCE FOR SELL{RESET}")
                            output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                            output_manager.print_static(f"{YELLOW}Coin: {coin_name}{RESET}")
                            output_manager.print_static(f"{YELLOW}Available Balance: {available_balance:.8f} {coin_name}{RESET}")
                            output_manager.print_static(f"{YELLOW}Total Balance: {wallet_balance:.8f} {coin_name}{RESET}")
                            if wallet_balance > available_balance:
                                locked = wallet_balance - available_balance
                                output_manager.print_static(f"{YELLOW}Locked Balance: {locked:.8f} {coin_name} (pending orders/deposits/withdrawals){RESET}")
                            # Show detailed minimum requirement
                            min_details = []
                            if min_order_size:
                                min_details.append(f"min size: {min_order_size:.8f}")
                            if min_order_value and min_qty_by_value:
                                min_details.append(f"min value: ${min_order_value:.2f} ({min_qty_by_value:.8f} @ ${current_price:.4f})")
                            
                            detail_str = f" ({', '.join(min_details)})" if min_details else ""
                            output_manager.print_static(f"{YELLOW}Minimum Required: {min_sell_amount:.8f} {coin_name}{detail_str}{RESET}")
                            output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                            output_manager.print_static(f"{YELLOW}⚠️  You need at least {min_sell_amount:.8f} {coin_name} to execute SELL orders (from {core.SELECTED_EXCHANGE}){RESET}")
                            
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            return
                        else:
                            # Show minimum order size info
                            min_info = f" (min: {min_sell_amount:.8f} from {core.SELECTED_EXCHANGE})" if min_sell_amount else ""
                            output_manager.print_static(f"{GREEN}✅ Coin Balance Check: {available_balance:.8f} {coin_name} available for SELL{min_info}{RESET}")
                            
                            # Store coin balance info for later use in enhanced_manual_trailing
                            # We'll pass this through a global or modify the function signature
                            # For now, store in a way that enhanced_manual_trailing can access
                            try:
                                import core
                                if not hasattr(core, '_sell_balance_info'):
                                    core._sell_balance_info = {}
                                core._sell_balance_info[symbol] = {
                                    'coin_name': coin_name,
                                    'available_balance': available_balance,
                                    'min_order_size': min_order_size if min_order_size else min_sell_amount,
                                    'min_order_value': min_order_value,
                                    'min_qty_by_value': min_qty_by_value
                                }
                            except Exception:
                                pass
                    except Exception as e:
                        # Error checking coin balance - warn but allow to continue
                        output_manager.print_static(f"{YELLOW}⚠️  Coin balance check failed: {str(e)}{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"{YELLOW}Continue without coin balance verification? (y/N): {RESET}").strip().lower()
                        if confirm != 'y':
                            output_manager.print_static(f"{YELLOW}SELL cancelled{RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            return
        
        # Check USDT balance before trading (for BUY mode 2 and 4, skip SELL mode 1)
        # Note: For trading_mode == "2" (Auto Rotation), balance check is already done earlier
        if mode in ["2", "4"] and trading_mode != "2":
            # Validate USDT balance before starting BUY trading
            from core import _init_exchange_client
            exchange_client = _init_exchange_client()
            if exchange_client:
                try:
                    account_info = exchange_client.get_account_info()
                    if account_info:
                        usdt_balance = account_info.get('usdt_balance', 0)
                        available_balance = usdt_balance
                        
                        # Check spot balances for USDT
                        spot_balances = account_info.get('spot_balances', [])
                        for balance in spot_balances:
                            if balance.get('coin') == 'USDT':
                                available_balance = balance.get('available', 0)
                                break
                        
                        # Minimum balance for trading - get from exchange API or use default
                        min_trading_balance = 5.0  # Default minimum $5 USDT (most exchanges)
                        
                        # Try to get actual minimum order value from exchange
                        try:
                            # Use a common trading pair to get minimum order value
                            test_symbol = "BTC_USDT"  # Most exchanges have BTC/USDT
                            if hasattr(exchange_client, 'get_min_order_value'):
                                min_order_value = exchange_client.get_min_order_value(test_symbol)
                                if min_order_value and min_order_value > 0:
                                    min_trading_balance = min_order_value
                        except Exception:
                            pass  # Use default if API call fails
                        
                        if available_balance < min_trading_balance:
                            output_manager.print_static(f"\n{RED}⚠️  INSUFFICIENT BALANCE FOR TRADING{RESET}")
                            output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                            if available_balance > 0:
                                output_manager.print_static(f"{YELLOW}Current USDT Balance: ${available_balance:.2f}{RESET}")
                                output_manager.print_static(f"{YELLOW}Minimum Required: ${min_trading_balance:.2f}{RESET}")
                                output_manager.print_static(f"{YELLOW}Shortage: ${min_trading_balance - available_balance:.2f}{RESET}")
                            else:
                                output_manager.print_static(f"{RED}No USDT balance available{RESET}")
                                output_manager.print_static(f"{YELLOW}Please deposit USDT to your {core.SELECTED_EXCHANGE} account{RESET}")
                            
                            output_manager.print_static(f"{RED}═══════════════════════════════════════════════════════════════{RESET}")
                            output_manager.print_static(f"{YELLOW}⚠️  Trading requires sufficient balance to execute orders{RESET}")
                            output_manager.print_static(f"{CYAN}💡 You can still use Mode 3 (Analysis Only) to view market data{RESET}")
                            
                            if not AUTO_CONTINUE:
                                confirm = input(f"\n{YELLOW}Continue anyway? (y/N): {RESET}").strip().lower()
                                if confirm != 'y':
                                    output_manager.print_static(f"{YELLOW}Trading cancelled due to insufficient balance{RESET}")
                                    if optimizer_task:
                                        optimizer_task.cancel()
                                    if flush_task:
                                        flush_task.cancel()
                                    return
                                else:
                                    output_manager.print_static(f"{YELLOW}⚠️  Proceeding with insufficient balance - trades may fail{RESET}")
                            else:
                                output_manager.print_static(f"{YELLOW}AUTO_CONTINUE enabled - proceeding despite insufficient balance{RESET}")
                        else:
                            # Store available balance for later use in BUY mode (non-rotation modes)
                            # Store in core module for enhanced_manual_trailing to access
                            try:
                                import core
                                if not hasattr(core, '_buy_balance_info'):
                                    core._buy_balance_info = {}
                                core._buy_balance_info['available_usdt'] = available_balance
                                core._buy_balance_info['min_trading_balance'] = min_trading_balance
                            except Exception:
                                pass
                    else:
                        # Account info not available - warn but allow to continue
                        output_manager.print_static(f"{YELLOW}⚠️  Cannot verify balance - account information unavailable{RESET}")
                        if not AUTO_CONTINUE:
                            confirm = input(f"{YELLOW}Continue without balance verification? (y/N): {RESET}").strip().lower()
                            if confirm != 'y':
                                output_manager.print_static(f"{YELLOW}Trading cancelled{RESET}")
                                if optimizer_task:
                                    optimizer_task.cancel()
                                if flush_task:
                                    flush_task.cancel()
                                return
                except Exception as e:
                    # Error checking balance - warn but allow to continue
                    output_manager.print_static(f"{YELLOW}⚠️  Balance check failed: {str(e)}{RESET}")
                    if not AUTO_CONTINUE:
                        confirm = input(f"{YELLOW}Continue without balance verification? (y/N): {RESET}").strip().lower()
                        if confirm != 'y':
                            output_manager.print_static(f"{YELLOW}Trading cancelled{RESET}")
                            if optimizer_task:
                                optimizer_task.cancel()
                            if flush_task:
                                flush_task.cancel()
                            return
        
        if mode == "3":
            output_manager.print_static(f"\n{MAGENTA}🧠 QUANTITATIVE AI ANALYSIS...{RESET}")
            print_ai_analysis(analysis, symbol)
            if optimizer_task:
                optimizer_task.cancel()
            if flush_task:
                flush_task.cancel()
            return
        elif mode == "4":
            output_manager.print_static(f"\n{MAGENTA}===================================================================================================={RESET}")
            output_manager.print_static(f"{MAGENTA}                              ⚡ AUTO-TRADER MODE{RESET}")
            output_manager.print_static(f"{MAGENTA}===================================================================================================={RESET}")
            output_manager.print_static(f"{CYAN}AI: {analysis.recommendation.value} {analysis.confidence:.0f}% │ {analysis.market_regime.value.title().replace('_', ' ')} │ Risk: {analysis.risk_level}{RESET}")
            # Import required functions
            from core import get_coin_type_range, calculate_coin_volatility, get_coin_display_name, TradingConfig
            coin_type = await get_coin_type_range(symbol)
            volatility = await calculate_coin_volatility(symbol)
            # Soft Filter: AI confidence threshold (50% - lower than before)
            # If confidence < 50%, give WARNING but don't block - let pre-buy validation decide
            min_confidence_threshold = 50.0  # Lower threshold for more flexibility
            if analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]:
                if analysis.confidence < min_confidence_threshold:
                    coin_type_display = get_coin_display_name(symbol, coin_type)
                    gap = min_confidence_threshold - analysis.confidence
                    output_manager.print_static(f"{YELLOW}⚠️  WARNING: AI Confidence {analysis.confidence:.0f}% < {min_confidence_threshold:.0f}% (soft threshold){RESET}")
                    output_manager.print_static(f"{CYAN}   Target: {min_confidence_threshold:.0f}% (need +{gap:.0f}%){RESET}")
                    output_manager.print_static(f"{CYAN}   ⚠️  Proceeding anyway - pre-buy validation will make final decision{RESET}")
                output_manager.print_static(f"\n{CYAN}ℹ️  Proceeding to Phase 2 (Pre-buy Validation){RESET}")
                output_manager.print_static(f"{YELLOW}💡 Pre-buy validation will perform comprehensive analysis{RESET}\n")
                delta = await calculate_ai_recommended_delta(
                    analysis,
                    { 'market_regime': analysis.market_regime, 'volatility': volatility },
                    "BUY",
                    symbol,
                    suppress_logs=True
                )
                smart_activation = await calculate_smart_activation_price(symbol, current_price, analysis, suppress_logs=True)
                # Import adaptive functions
                from core import (
                    get_adaptive_stop_loss, get_adaptive_offset, get_adaptive_poll_interval,
                    get_adaptive_timeout, get_adaptive_take_profit, TradingConfig, hybrid_trailing_buy, fmt, safe_division
                )
                coin_type = await get_coin_type_range(symbol)
                volatility = await calculate_coin_volatility(symbol)
                adaptive_stop_loss = get_adaptive_stop_loss(coin_type, volatility)
                adaptive_offset = get_adaptive_offset(coin_type, volatility)
                adaptive_poll_interval = get_adaptive_poll_interval(coin_type, volatility)
                # Use get_adaptive_timeout with trailing mode support
                adaptive_timeout = get_adaptive_timeout(coin_type, volatility, use_trailing_tp=True)
                # Note: Activation price will be recalculated and displayed inside hybrid_trailing_buy
                # Don't show preliminary activation price here to avoid confusion
                # The final activation price will be shown by calculate_smart_activation_price inside hybrid_trailing_buy
                timeout_display = f"{adaptive_timeout}min" if adaptive_timeout else "None (patient)"
                output_manager.print_static(f"{CYAN}🎯 ADAPTIVE PARAMS: SL={adaptive_stop_loss:.1f}% | Timeout={timeout_display} | Offset={adaptive_offset:.1f}% | Poll={adaptive_poll_interval:.1f}s{RESET}")
                if rotation_mode:
                    rotation_count = 0
                    max_rotations = 50
                    # Use rotation_analysis from scan (already set in outer scope if scan was successful)
                    # This ensures consistency between scan and rotation (no re-analysis)
                    # rotation_analysis is already initialized in outer scope (line 1553)
                    # If scan was successful, it will have the analysis; otherwise it will be None
                    output_manager.print_static(f"\n{MAGENTA}🔄 AUTO-ROTATION ACTIVE{RESET}")
                    output_manager.print_static(f"{CYAN}Bot will continuously rotate through best coins from coinlist.txt{RESET}\n")
                    while rotation_count < max_rotations:
                        rotation_count += 1
                        # Get current coin data with error handling
                        try:
                            coin_type_current = await get_coin_type_range(symbol)
                            volatility_current = await calculate_coin_volatility(symbol)
                            
                            # FIX: Use analysis from scan (if available) instead of re-analyzing
                            # This ensures consistency between scan and rotation
                            if rotation_analysis is not None:
                                analysis_current = rotation_analysis
                                output_manager.print_static(f"{CYAN}📊 Using analysis from scan: {analysis_current.recommendation.value} ({analysis_current.confidence:.1f}% confidence){RESET}")
                            else:
                                # Fallback: Re-analyze if scan analysis not available
                                analysis_current = await analyze_hold_wait_buy_ai(symbol)
                                output_manager.print_static(f"{YELLOW}⚠️ Fresh analysis: {analysis_current.recommendation.value} ({analysis_current.confidence:.1f}% confidence){RESET}")
                            
                            current_price_current = await get_price_optimized(symbol)
                            
                            # CRITICAL: Validate all data before proceeding
                            if current_price_current is None:
                                output_manager.print_static(f"{RED}❌ Failed to get price for {symbol}. Skipping rotation...{RESET}")
                                # Skip to next coin scan
                                break
                            if analysis_current is None:
                                output_manager.print_static(f"{RED}❌ Failed to get analysis for {symbol}. Skipping rotation...{RESET}")
                                # Skip to next coin scan
                                break
                            
                            resistance_current = analysis_current.price_targets.get('short_term', current_price_current * 1.03)
                            upside_potential = safe_division((resistance_current - current_price_current), current_price_current, 0.03) * 100
                            
                            # CRITICAL: Validate analysis quality before proceeding
                            # Skip coin jika recommendation bukan BUY/STRONG_BUY atau confidence terlalu rendah
                            if analysis_current.recommendation not in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]:
                                output_manager.print_static(f"{YELLOW}⚠️ Recommendation {analysis_current.recommendation.value} (confidence: {analysis_current.confidence:.0f}%) - Skipping {symbol}...{RESET}")
                                output_manager.print_static(f"{YELLOW}🔄 ROTATION MODE: Skipping {symbol} (unfavorable conditions: {analysis_current.recommendation.value}) → Will scan for next coin{RESET}")
                                await mark_coin_skip_rotation(symbol, rotation_count, skip_rotations=2)
                                skip_remaining = await get_skip_rotations_remaining(symbol, rotation_count)
                                output_manager.print_static(f"{CYAN}⏸️ {symbol} skipped for {skip_remaining} more rotation(s){RESET}")
                                break  # Skip to next coin scan
                            
                            # Note: Check for HOLD/WAIT with low confidence is redundant here
                            # because we already check for BUY/STRONG_BUY above (line 2180-2186)
                            # This check is kept for safety but should not be reached if previous check works correctly
                            
                        except Exception as e:
                            output_manager.print_static(f"{RED}❌ Error getting coin data for {symbol}: {str(e)}. Skipping rotation...{RESET}")
                            # Skip to next coin scan
                            break
                        
                        # Calculate adaptive parameters for CURRENT coin (must be inside loop)
                        adaptive_offset_current = get_adaptive_offset(coin_type_current, volatility_current)
                        adaptive_poll_interval_current = get_adaptive_poll_interval(coin_type_current, volatility_current)
                        market_regime_for_timeout = analysis_current.market_regime
                        regime_key = market_regime_for_timeout.name
                        # Use get_adaptive_timeout with trailing mode support
                        from core import get_adaptive_timeout
                        adaptive_timeout_current = get_adaptive_timeout(
                            coin_type_current, 
                            volatility_current, 
                            rotation_mode=True, 
                            use_trailing_tp=use_trailing_tp
                        )
                        
                        # For Fixed Mode: use scalping TP/SL (international standard for scalpers)
                        # For Trailing Mode: use regular adaptive TP/SL
                        if use_trailing_tp:
                            adaptive_tp = get_adaptive_take_profit(coin_type_current, volatility_current, upside_potential, analysis_current.market_regime)
                            adaptive_sl_current = get_adaptive_stop_loss(coin_type_current, volatility_current)
                        else:
                            # Fixed Mode: Use scalping TP/SL (international standard)
                            from core import get_scalping_take_profit, get_scalping_stop_loss
                            adaptive_tp = get_scalping_take_profit(coin_type_current, volatility_current, upside_potential, analysis_current.market_regime)
                            adaptive_sl_current = get_scalping_stop_loss(coin_type_current, volatility_current)
                        
                        # Both modes use smart activation price calculated by bot
                        # Fixed Mode: uses skip_wait=True for faster entry after activation
                        # Trailing Mode: uses skip_wait=False for optimal entry timing
                        smart_activation_current = await calculate_smart_activation_price(symbol, current_price_current, analysis_current)
                        
                        # STRATEGI 3 (BALANCED): Handle kontradiksi AI STRONG_BUY vs Buy Pressure VERY WEAK
                        # Step 1: Deteksi kontradiksi (AI STRONG_BUY + Buy Pressure VERY WEAK)
                        initial_flow_score = None
                        buy_pressure_adjusted = False
                        if analysis_current.recommendation == ActionRecommendation.STRONG_BUY and analysis_current.confidence >= 75:
                            try:
                                from core import get_order_flow_optimized, calculate_flow_score_from_buy_pressure
                                initial_order_flow = await get_order_flow_optimized(symbol, limit=50)
                                initial_buy_pressure = initial_order_flow.get('buy_pressure', 50.0)
                                
                                # Debug logging untuk buy pressure 0.0% atau 50.0% dengan trade_count > 0
                                if initial_buy_pressure == 0.0 or (initial_buy_pressure == 50.0 and initial_order_flow.get('trade_count', 0) > 0):
                                    trade_count = initial_order_flow.get('trade_count', 0)
                                    buy_volume = initial_order_flow.get('buy_volume', 0)
                                    sell_volume = initial_order_flow.get('sell_volume', 0)
                                    total_volume = buy_volume + sell_volume
                                    output_manager.print_static(
                                        f"{YELLOW}🔍 DEBUG: Buy Pressure {initial_buy_pressure:.1f}% detected - trade_count={trade_count}, buy_vol={buy_volume:.2f}, sell_vol={sell_volume:.2f}, total_vol={total_volume:.2f}{RESET}"
                                    )
                                    if total_volume == 0 and trade_count > 0:
                                        output_manager.print_static(
                                            f"{YELLOW}⚠️ INFO: All {trade_count} trades have invalid data (price=0 or qty=0) - using default buy_pressure=50.0%{RESET}"
                                        )
                                    elif initial_buy_pressure == 0.0 and total_volume > 0:
                                        output_manager.print_static(
                                            f"{CYAN}✅ VALID: All trades in last 30s are SELL (buy_volume=0, total_volume>0){RESET}"
                                        )
                                    elif initial_buy_pressure == 50.0 and total_volume > 0:
                                        output_manager.print_static(
                                            f"{CYAN}✅ VALID: Buy pressure 50.0% (neutral - equal buy/sell volume){RESET}"
                                        )
                                
                                initial_flow_score = await calculate_flow_score_from_buy_pressure(
                                    symbol, initial_buy_pressure, analysis_current, volatility_current
                                )
                                
                                # Kontradiksi terdeteksi: AI STRONG_BUY tapi Buy Pressure VERY WEAK
                                if initial_flow_score <= 4.0:
                                    output_manager.print_static(
                                        f"\n{YELLOW}{'═'*70}{RESET}"
                                    )
                                    output_manager.print_static(
                                        f"{YELLOW}⚠️ KONTRADIKSI TERDETEKSI: AI STRONG_BUY ({analysis_current.confidence:.0f}%) vs Buy Pressure VERY WEAK ({initial_flow_score:.1f}/20){RESET}"
                                    )
                                    output_manager.print_static(
                                        f"{CYAN}📊 Initial Buy Pressure: {initial_buy_pressure:.1f}%{RESET}"
                                    )
                                    # Adaptive delay based on trading mode
                                    # Trailing mode: shorter delay (20s) for faster entry
                                    # Fixed mode: longer delay (30s) for more confirmation
                                    buy_pressure_delay = 20 if use_trailing_tp else 30
                                    output_manager.print_static(
                                        f"{CYAN}🔄 STRATEGI 3 (BALANCED): Re-checking buy pressure after {buy_pressure_delay} seconds...{RESET}"
                                    )
                                    output_manager.print_static(
                                        f"{YELLOW}{'═'*70}{RESET}\n"
                                    )
                                    
                                    # Step 2: Adaptive delay based on trading mode
                                    await asyncio.sleep(buy_pressure_delay)
                                    
                                    # Step 3: Re-check buy pressure
                                    fresh_order_flow = await get_order_flow_optimized(symbol, limit=50)
                                    fresh_buy_pressure = fresh_order_flow.get('buy_pressure', 50.0)
                                    fresh_flow_score = await calculate_flow_score_from_buy_pressure(
                                        symbol, fresh_buy_pressure, analysis_current, volatility_current
                                    )
                                    
                                    output_manager.print_static(
                                        f"{CYAN}📊 Fresh Buy Pressure: {fresh_buy_pressure:.1f}% (Flow Score: {fresh_flow_score:.1f}/20){RESET}"
                                    )
                                    
                                    # Step 4: Decision
                                    if fresh_flow_score >= 8.0:
                                        # Buy pressure membaik → TRADE normal
                                        output_manager.print_static(
                                            f"{GREEN}✅ Buy Pressure MEMBAIK ({fresh_flow_score:.1f}/20 >= 8.0) → TRADE NORMAL{RESET}\n"
                                        )
                                    else:
                                        # Buy pressure masih VERY WEAK → TRADE dengan entry discount 2x
                                        output_manager.print_static(
                                            f"{YELLOW}⚠️ Buy Pressure masih VERY WEAK ({fresh_flow_score:.1f}/20 < 8.0) → TRADE dengan ENTRY DISCOUNT 2x{RESET}"
                                        )
                                        
                                        # Calculate 2x discount
                                        original_discount_pct = safe_division(
                                            (current_price_current - smart_activation_current), 
                                            current_price_current, 0.0
                                        ) * 100
                                        
                                        # Apply 2x discount (reduce activation price further)
                                        discount_multiplier = 2.0
                                        new_discount_pct = original_discount_pct * discount_multiplier
                                        # Ensure discount doesn't exceed max (safety check)
                                        max_discount_pct = 10.0  # Safety limit
                                        new_discount_pct = min(new_discount_pct, max_discount_pct)
                                        
                                        # Recalculate activation price with 2x discount
                                        smart_activation_current = current_price_current * (1 - new_discount_pct / 100)
                                        
                                        output_manager.print_static(
                                            f"{CYAN}   Original Discount: {original_discount_pct:.2f}% → New Discount: {new_discount_pct:.2f}% (2x){RESET}"
                                        )
                                        output_manager.print_static(
                                            f"{CYAN}   New Activation Price: ${smart_activation_current:.8f} (${current_price_current:.8f} → -{new_discount_pct:.2f}%){RESET}"
                                        )
                                        output_manager.print_static(
                                            f"{YELLOW}   ⚠️ NOTE: Buy pressure akan di-check lagi sebelum actual buy execution{RESET}"
                                        )
                                        output_manager.print_static(
                                            f"{YELLOW}   → Jika membaik: Discount akan dikurangi otomatis{RESET}"
                                        )
                                        output_manager.print_static(
                                            f"{YELLOW}   → Jika memburuk: Trade akan di-skip untuk safety{RESET}\n"
                                        )
                                        buy_pressure_adjusted = True
                                        # Store adjustment info for final check
                                        import core
                                        if not hasattr(core, '_buy_pressure_adjustments'):
                                            core._buy_pressure_adjustments = {}
                                        core._buy_pressure_adjustments[symbol] = {
                                            'original_discount': original_discount_pct,
                                            'adjusted_discount': new_discount_pct,
                                            'original_activation': smart_activation_current / (1 - new_discount_pct / 100) if new_discount_pct > 0 else current_price_current,
                                            'adjusted_activation': smart_activation_current,
                                            'initial_flow_score': initial_flow_score,
                                            'fresh_flow_score': fresh_flow_score,
                                            'needs_final_check': True
                                        }
                            except Exception as e:
                                output_manager.print_static(
                                    f"{YELLOW}⚠️ Error in balanced strategy (buy pressure check): {e}{RESET}"
                                )
                                # Continue with normal trading if error occurs
                        
                        # For Fixed Mode (scalping): NO delta - execute immediately after activation
                        # For Trailing Mode: use full AI-recommended delta for trailing buy
                        if use_trailing_tp:
                            delta_current = await calculate_ai_recommended_delta(analysis_current, {}, "BUY", symbol)
                        else:
                            # Fixed Mode (scalping): Set delta to 0 for immediate entry after activation
                            # No trailing buy mechanism - execute buy as soon as activation price is reached
                            delta_current = 0.0
                        
                        # Get buy amount for Auto Rotation (set by user input earlier)
                        buy_amount_rotation = None
                        try:
                            import core
                            trading_amount_rotation = getattr(core, '_buy_balance_info', {}).get('trading_amount_rotation', None)
                            
                            if trading_amount_rotation:
                                buy_amount_rotation = trading_amount_rotation
                        except Exception:
                            pass  # If balance info not available, use None (no buy_amount)
                        
                        # Format: Responsive Compact - Cocok untuk layar VS Code & HP
                        output_manager.print_static(f"\n{GREEN}{'='*70}{RESET}")
                        output_manager.print_static(f"{GREEN}🎯 ROTATION #{rotation_count}: {symbol}{RESET}")
                        output_manager.print_static(f"{CYAN}   AI: {analysis_current.confidence:.0f}% │ Type: {coin_type_current} │ Vol: {volatility_current:.0f}%{RESET}")
                        if buy_amount_rotation:
                            output_manager.print_static(f"{CYAN}   Amount: ${buy_amount_rotation:.2f} USDT{RESET}")
                        if use_trailing_tp:
                            output_manager.print_static(f"{CYAN}   Exit: TRAILING TP │ SL: {adaptive_sl_current:.1f}%{RESET}")
                            output_manager.print_static(f"{MAGENTA}   Strategy: Maximize profit │ Ride trends │ Auto profit-lock{RESET}")
                        else:
                            discount_pct = safe_division((current_price_current - smart_activation_current), current_price_current, 0.0) * 100
                            output_manager.print_static(f"{CYAN}   TP: {adaptive_tp:.2f}% (FIXED) │ SL: {adaptive_sl_current:.2f}%{RESET}")
                            risk_reward = adaptive_tp / adaptive_sl_current if adaptive_sl_current > 0 else 0
                            output_manager.print_static(f"{MAGENTA}   Strategy: Scalping │ R:R 1:{risk_reward:.1f} │ Quick exits{RESET}")
                            output_manager.print_static(f"{CYAN}   Entry: ${fmt(smart_activation_current)} (${fmt(current_price_current)} → -{discount_pct:.1f}%){RESET}")
                            output_manager.print_static(f"{CYAN}   ⚡ No delta - Immediate entry (scalping){RESET}")
                        output_manager.print_static(f"{GREEN}{'='*70}{RESET}\n")
                        # For Fixed Mode (scalping): use direct entry (skip_wait=True) for faster execution
                        # For Trailing Mode: use trailing buy (skip_wait=False) for optimal entry
                        skip_wait_for_scalping = not use_trailing_tp  # True for Fixed Mode, False for Trailing Mode
                        
                        await hybrid_trailing_buy(
                            symbol=symbol,
                            delta_percent=delta_current,
                            activate_price=smart_activation_current,
                            advance=True,
                            offset=adaptive_offset_current,
                            skip_wait=skip_wait_for_scalping,
                            poll_interval=adaptive_poll_interval_current,
                            auto_trailing_stop=True,
                            stop_loss_percent=adaptive_sl_current,
                            dynamic_ai_stop=True,
                            auto_mode=True,
                            timeout_minutes=adaptive_timeout_current,
                            take_profit_percent=adaptive_tp if not use_trailing_tp else None,
                            rotation_mode=True,
                            analysis=analysis_current,  # Pass analysis to avoid re-analysis inconsistency
                            buy_amount=buy_amount_rotation,
                            current_rotation=rotation_count
                        )
                        # Note: Coin skip rotation is handled inside hybrid_trailing_buy if coin is skipped
                        # Only mark cooldown if coin was successfully traded (not skipped)
                        # For rotation mode, we use skip rotation instead of cooldown
                        # Only show summary if there are trades (skip summary if coin was skipped)
                        portfolio_manager.print_session_summary()
                        output_manager.print_static(f"\n{CYAN}{'='*70}{RESET}")
                        output_manager.print_static(f"{MAGENTA}🔄 ROTATION #{rotation_count} COMPLETE{RESET}")
                        output_manager.print_static(f"{CYAN}{'='*70}{RESET}")
                        # Continuously scan until a suitable coin is found
                        scan_attempts = 0
                        while True:
                            scan_attempts += 1
                            # No confidence filter - let bot pick best coin from scan, filter in pre-buy validation
                            symbol, analysis, all_results = await scan_best_coin_from_watchlist(watchlist, exclude_cooldown=True, min_confidence=0.0, current_rotation=rotation_count, rotation_mode=True)
                            if symbol is not None and analysis is not None:
                                # CRITICAL: Store analysis from scan for use in next rotation loop iteration
                                # This ensures consistency between scan and rotation (no re-analysis)
                                rotation_analysis = analysis  # Update for next rotation
                                break
                            wait_time = 60
                            output_manager.print_static(f"{YELLOW}⚠️ No coins meet criteria (attempt {scan_attempts}){RESET}")
                            output_manager.print_static(f"{CYAN}⏳ Waiting {wait_time}s for market conditions to improve...{RESET}")
                            await asyncio.sleep(wait_time)
                        # Update adaptive parameters for next rotation (these will be used in next iteration)
                        # Note: These are calculated here but will be recalculated at start of next loop iteration
                        # This is kept for compatibility but actual values used are from inside the loop
                        try:
                            current_price = await get_price_optimized(symbol)
                            if current_price:
                                delta = await calculate_ai_recommended_delta(analysis, {}, "BUY", symbol)
                                smart_activation = await calculate_smart_activation_price(symbol, current_price, analysis)
                                # Update for next iteration (though will be recalculated anyway)
                                adaptive_offset = get_adaptive_offset(await get_coin_type_range(symbol), await calculate_coin_volatility(symbol))
                                adaptive_poll_interval = get_adaptive_poll_interval(await get_coin_type_range(symbol), await calculate_coin_volatility(symbol))
                                adaptive_timeout = get_adaptive_timeout(await get_coin_type_range(symbol), await calculate_coin_volatility(symbol), rotation_mode=True)
                        except Exception:
                            # If update fails, continue anyway - values will be recalculated at start of next loop
                            pass
                        output_manager.print_static(f"{GREEN}💰 Next coin ready. Continuing rotation...{RESET}\n")
                        await asyncio.sleep(2)
                    if rotation_count >= max_rotations:
                        output_manager.print_static(f"{YELLOW}⏹️ Max rotations ({max_rotations}) reached. Session complete.{RESET}")
                        # Force show summary at end of session (even if no trades)
                        portfolio_manager.print_session_summary(force_show=True)
                else:
                    output_manager.print_static(f"\n{CYAN}🎯 MODE 1 SWING TRADING:{RESET}")
                    output_manager.print_static(f"   SL: {adaptive_stop_loss:.1f}% (Adaptive)")
                    output_manager.print_static(f"   Exit: TRAILING STOP (Maximize profit in trends!) | Entry: TRAILING BUY{RESET}\n")
                await hybrid_trailing_buy(
                    symbol=symbol,
                    delta_percent=delta,
                    activate_price=smart_activation,
                    advance=True,
                    offset=adaptive_offset,
                    skip_wait=False,
                    poll_interval=adaptive_poll_interval,
                    auto_trailing_stop=True,
                    stop_loss_percent=adaptive_stop_loss,
                        dynamic_ai_stop=True,
                    auto_mode=True,
                    timeout_minutes=adaptive_timeout
                )
            else:
                # Soft Filter: AI confidence threshold (50% - lower than before)
                # If confidence < 50%, give WARNING but don't block - let pre-buy validation decide
                min_confidence_threshold = 50.0  # Lower threshold for more flexibility
                if analysis.confidence < min_confidence_threshold:
                    coin_type_display = get_coin_display_name(symbol, coin_type)
                    gap = min_confidence_threshold - analysis.confidence
                    output_manager.print_static(f"{YELLOW}⚠️  WARNING: AI Confidence {analysis.confidence:.0f}% < {min_confidence_threshold:.0f}% (soft threshold){RESET}")
                    output_manager.print_static(f"{CYAN}   Target: {min_confidence_threshold:.0f}% (need +{gap:.0f}%){RESET}")
                    output_manager.print_static(f"{CYAN}   ⚠️  Proceeding anyway - pre-buy validation will make final decision{RESET}")
                output_manager.print_static(f"\n{GREEN}✅ Proceeding to Phase 2 (Pre-buy Validation){RESET}")
                output_manager.print_static(f"{CYAN}💡 Best coin selected from scan - pre-buy validation will filter if needed{RESET}")
                output_manager.print_static(f"{GREEN}🚀 Starting trading with pre-buy validation!{RESET}\n")
                # Import adaptive functions
                from core import (
                    calculate_ai_recommended_delta, calculate_smart_activation_price,
                    get_coin_type_range, calculate_coin_volatility,
                    get_adaptive_stop_loss, get_adaptive_timeout,
                    get_adaptive_offset, get_adaptive_poll_interval,
                    hybrid_trailing_buy, safe_division, fmt
                )
                delta = await calculate_ai_recommended_delta(analysis, {}, "BUY", symbol)
                smart_activation = await calculate_smart_activation_price(symbol, current_price, analysis)
                coin_type = await get_coin_type_range(symbol)
                volatility = await calculate_coin_volatility(symbol)
                adaptive_stop_loss = get_adaptive_stop_loss(coin_type, volatility)
                adaptive_timeout = get_adaptive_timeout(coin_type, volatility, use_trailing_tp=True)
                adaptive_offset = get_adaptive_offset(coin_type, volatility)
                adaptive_poll_interval = get_adaptive_poll_interval(coin_type, volatility)
                discount_pct = safe_division((current_price - smart_activation), current_price, 0.0) * 100
                output_manager.print_static(f"{CYAN}🧠 SMART ACTIVATION: ${fmt(current_price)} → ${fmt(smart_activation)} ({discount_pct:.1f}% discount){RESET}")
                output_manager.print_static(f"{CYAN}🎯 ADAPTIVE PARAMS: SL={adaptive_stop_loss:.1f}% | Timeout={adaptive_timeout}min | Offset={adaptive_offset:.1f}% | Poll={adaptive_poll_interval:.1f}s{RESET}")
                await hybrid_trailing_buy(
                    symbol=symbol,
                    delta_percent=delta,
                    activate_price=smart_activation,
                    advance=True,
                    offset=adaptive_offset,
                    skip_wait=False,
                    poll_interval=adaptive_poll_interval,
                    auto_trailing_stop=True,
                    stop_loss_percent=adaptive_stop_loss,
                    dynamic_ai_stop=True,
                    auto_mode=True,
                    timeout_minutes=adaptive_timeout
                )
                if optimizer_task:
                    optimizer_task.cancel()
                return
        elif mode == "5":
            output_manager.print_static(f"{CYAN}👋 Exiting bot... Goodbye!{RESET}")
            if optimizer_task:
                optimizer_task.cancel()
            if flush_task:
                flush_task.cancel()
            return
        elif mode in ["1", "2"]:
            strategy_type = "SELL" if mode == "1" else "BUY"
            
            # For BUY mode (2): Auto AI trailing buy - activation and delta from AI, exit after buy
            if mode == "2":  # BUY mode - Auto AI
                # Get USDT amount to trade
                buy_amount = None
                try:
                    import core
                    available_usdt = getattr(core, '_buy_balance_info', {}).get('available_usdt', 0)
                    min_trading_balance = getattr(core, '_buy_balance_info', {}).get('min_trading_balance', 5.0)
                    
                    if available_usdt > 0:
                        output_manager.print_static(f"\n{CYAN}Available USDT Balance: ${available_usdt:.2f}{RESET}")
                        output_manager.print_static(f"{CYAN}Minimum Order Value: ${min_trading_balance:.2f} USDT{RESET}")
                        
                        while True:
                            try:
                                amount_input = input(f"{YELLOW}Enter USDT amount to trade (max: ${available_usdt:.2f}): {RESET}").strip()
                                if not amount_input:
                                    output_manager.print_static(f"{RED}Amount cannot be empty. Please enter a valid amount.{RESET}")
                                    continue
                                
                                buy_amount = float(amount_input)
                                
                                # Validate amount
                                if buy_amount <= 0:
                                    output_manager.print_static(f"{RED}Amount must be greater than 0.{RESET}")
                                    continue
                                
                                if buy_amount < min_trading_balance:
                                    output_manager.print_static(f"{RED}Amount ${buy_amount:.2f} is less than minimum order value ${min_trading_balance:.2f} USDT{RESET}")
                                    continue
                                
                                if buy_amount > available_usdt:
                                    output_manager.print_static(f"{RED}Amount ${buy_amount:.2f} exceeds available balance ${available_usdt:.2f} USDT{RESET}")
                                    continue
                                
                                # Valid amount
                                output_manager.print_static(f"{GREEN}✅ Amount confirmed: ${buy_amount:.2f} USDT{RESET}")
                                break
                            except ValueError:
                                output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")
                                continue
                    else:
                        # If balance not available, ask for amount without validation
                        while True:
                            try:
                                amount_input = input(f"{YELLOW}Enter USDT amount to trade: {RESET}").strip()
                                if not amount_input:
                                    output_manager.print_static(f"{RED}Amount cannot be empty. Please enter a valid amount.{RESET}")
                                    continue
                                buy_amount = float(amount_input)
                                if buy_amount <= 0:
                                    output_manager.print_static(f"{RED}Amount must be greater than 0.{RESET}")
                                    continue
                                break
                            except ValueError:
                                output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")
                                continue
                except Exception as e:
                    output_manager.print_static(f"{YELLOW}⚠️  Error getting balance info: {str(e)}{RESET}")
                    # Ask for amount without validation
                    while True:
                        try:
                            amount_input = input(f"{YELLOW}Enter USDT amount to trade: {RESET}").strip()
                            if not amount_input:
                                output_manager.print_static(f"{RED}Amount cannot be empty. Please enter a valid amount.{RESET}")
                                continue
                            buy_amount = float(amount_input)
                            if buy_amount <= 0:
                                output_manager.print_static(f"{RED}Amount must be greater than 0.{RESET}")
                                continue
                            break
                        except ValueError:
                            output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")
                            continue
            
            # Validate position size using actual buy_amount, not hardcoded value
            # Calculate quantity from buy_amount and current_price
            from core import safe_division
            if mode == "2" and buy_amount is not None:
                # For BUY mode, calculate size from buy_amount
                position_size = safe_division(buy_amount, current_price, 0.0)
                position_value = buy_amount  # Position value is the USDT amount
            else:
                # For SELL mode or if buy_amount not available, use default
                position_size = 0.1
                position_value = position_size * current_price
            
            if not await portfolio_manager.can_open_position(symbol, position_size, current_price):
                output_manager.print_static(f"{RED}❌ Cannot open new position - portfolio limits reached{RESET}")
                if optimizer_task:
                    optimizer_task.cancel()
                return
            
            if mode == "2":  # Auto AI BUY mode
                # Auto AI trailing buy - activation and delta determined by AI
                output_manager.print_static(f"\n{MAGENTA}🤖 AUTO AI TRAILING BUY MODE{RESET}")
                output_manager.print_static(f"{CYAN}AI will determine activation price and delta automatically{RESET}")
                output_manager.print_static(f"{CYAN}After successful buy, will exit immediately{RESET}\n")
                
                # Calculate AI activation price
                from core import calculate_smart_activation_price, calculate_ai_recommended_delta, safe_division
                smart_activation = await calculate_smart_activation_price(symbol, current_price, analysis, suppress_logs=True)
                ai_delta = await calculate_ai_recommended_delta(analysis, {}, 'BUY', symbol)
                
                discount_pct = safe_division((current_price - smart_activation), current_price, 0.0) * 100
                output_manager.print_static(f"{GREEN}💰 Current Price: ${fmt(current_price)}{RESET}")
                output_manager.print_static(f"{CYAN}🧠 AI Activation Price: ${fmt(smart_activation)} ({discount_pct:.1f}% discount){RESET}")
                output_manager.print_static(f"{CYAN}🎯 AI Delta: {ai_delta:.1f}%{RESET}")
                output_manager.print_static(f"{CYAN}💵 Trade Amount: ${buy_amount:.2f} USDT{RESET}\n")
                
                # Execute auto trailing buy with AI parameters
                from core import auto_ai_trailing_buy
                await auto_ai_trailing_buy(
                    symbol=symbol,
                    activate_price=smart_activation,
                    delta_percent=ai_delta,
                    buy_amount=buy_amount,
                    current_price=current_price,
                    analysis=analysis
                )
            else:
                # SELL mode - manual input
                await enhanced_manual_trailing(symbol, strategy_type, current_price, initial_volatility, analysis, buy_amount=None)
        else:
            output_manager.print_static(f"{RED}Invalid mode selected. Please choose 1-6.{RESET}")
        if optimizer_task:
            optimizer_task.cancel()
        if flush_task:
            flush_task.cancel()
    except KeyboardInterrupt:
        output_manager.print_static(f"\n{YELLOW}⏹️ Operation cancelled by user (Ctrl+C){RESET}")
        # Force show summary at end of script (even if no trades)
        portfolio_manager.print_session_summary(force_show=True)
        if portfolio_manager.positions:
            output_manager.print_static(f"\n{YELLOW}⚠️  You have {len(portfolio_manager.positions)} tracked position(s){RESET}")
            close_choice = input(f"{RED}Close all positions before exiting? (yes/no): {RESET}").strip().lower()
            if close_choice in ['yes', 'y']:
                await emergency_close_all_positions(dry_run=False)
            else:
                output_manager.print_static(f"{YELLOW}⚠️ Positions not closed - exiting anyway{RESET}")
                output_manager.print_static(f"{CYAN}💡 Remember to manage your positions manually if needed{RESET}")
        performance_monitor.print_performance_report()
        try:
            from core import _init_ws_manager
            ws_manager = _init_ws_manager()
            if ws_manager:
                await ws_manager.stop_all_streams()
        except Exception as e:
            output_manager.print_static(f"{YELLOW}⚠️ WebSocket cleanup warning: {e}{RESET}")
        if optimizer_task:
            optimizer_task.cancel()
        if flush_task:
            flush_task.cancel()
    except Exception as e:
        # Ensure color constants are available in exception handler
        try:
            from core import RED, RESET, output_manager, error_handler
        except ImportError:
            RED = "\033[91m"
            RESET = "\033[0m"
            from core import output_manager, error_handler
        
        output_manager.print_static(f"{RED}❌ Main execution error: {e}{RESET}")
        import time as time_module
        error_handler.record_error("Main_Execution_Error", "Main execution failed", {
            "error": str(e),
            "timestamp": time_module.time()
        })
        try:
            from core import _init_ws_manager, task_manager
            ws_manager = _init_ws_manager()
            if ws_manager:
                await ws_manager.stop_all_streams()
        except Exception as e:
            # Ensure color constants are available
            try:
                from core import YELLOW, RESET
            except ImportError:
                YELLOW = "\033[93m"
                RESET = "\033[0m"
            output_manager.print_static(f"{YELLOW}⚠️ WebSocket cleanup warning: {e}{RESET}")
        try:
            from core import task_manager
            await task_manager.cancel_all_tasks("ALL")
        except Exception as e:
            # Ensure color constants are available
            try:
                from core import YELLOW, RESET
            except ImportError:
                YELLOW = "\033[93m"
                RESET = "\033[0m"
            output_manager.print_static(f"{YELLOW}⚠️ Task cleanup warning: {e}{RESET}")
        output_manager.stop_live_update()
        performance_monitor.print_performance_report()
        if optimizer_task:
            optimizer_task.cancel()
        if flush_task:
            flush_task.cancel()

if __name__ == "__main__":
    try:
        # Clear screen at startup DISABLED - keep terminal history visible
        # import os
        # os.system('clear' if os.name != 'nt' else 'cls')
        
        # Config is loaded automatically via CONFIG_FILE in core.py
        # No need to call load_config() separately
        
        # ENABLE_PARALLEL_PROCESSING is imported from core via "from core import *"
        if 'ENABLE_PARALLEL_PROCESSING' in globals() and ENABLE_PARALLEL_PROCESSING:
            import sys
            if sys.platform == 'linux':
                try:
                    import uvloop
                    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
                    # uvloop message removed to keep output clean
                except ImportError:
                    pass
        asyncio.run(main_hybrid_enhanced())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        try:
            from core import output_manager, RED, RESET, now_ts, error_handler
            output_manager.print_static(f"\n{RED}❌ QUANTITATIVE SYSTEM ERROR: {e} [{now_ts()}]{RESET}")
            error_handler.record_error("System_Error", "Quantitative system failed", {"error": str(e)})
        except:
            print(f"\n❌ QUANTITATIVE SYSTEM ERROR: {e}")
        import traceback
        error_details = traceback.format_exc()
        try:
            from core import RED, RESET
            print(f"{RED}Error details: {error_details}{RESET}")
        except:
            print(f"Error details: {error_details}")
    finally:
        # Skip all cleanup output - user requested to remove verbose cleanup messages
        # Only perform cleanup silently without printing
        try:
            if PERSIST_CANDLES:
                from core import _init_ws_manager
                ws_manager = _init_ws_manager()
                if ws_manager and hasattr(ws_manager, 'candle_cache'):
                    for cache_key, cache_data in ws_manager.candle_cache.items():
                        try:
                            parts = cache_key.rsplit('_', 1)
                            if len(parts) == 2:
                                symbol, interval = parts
                                candles = cache_data.get('candles', [])
                                if candles:
                                    save_persisted_candles(symbol, interval, candles)
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            cleanup_async_tasks_and_websockets()
        except Exception:
            pass
        try:
            ml_enhancer.clear_all_data()
        except Exception:
            pass
        try:
            risk_metrics.var_calculator.clear_all_data()
        except Exception:
            pass
        try:
            anti_panic_dump.reset()
        except Exception:
            pass
