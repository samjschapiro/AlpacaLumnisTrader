from .utils import standardize, getDailyVol

from lumnisfactors import LumnisFactors

from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.historical import CryptoHistoricalDataClient

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import TakeProfitRequest, StopLossRequest

from datetime import datetime
from tqdm import tqdm
import pandas as pd
import numpy as np
import time


class AlpacaLumnisTrader():
    """
    AlpacaLumnisTrader

    An interface that connects the Lumnis API with the Alpaca API to facilitate live and paper trading

    Parameters
    ----------
        binance_api_key : str
            String key used to connect to Binance API

        binance_secret_key : str
            String secret key used to connect to Binance

        lumnis_api_key : str
            String key used to connect to Lumnis API

        coins : list
            Cryptocurrencies used for this trader

        paper : bool, default=True
            Boolean flag that decides whether paper trading or live trading.

        time_frame : str, default='min'
            Time frame of sample observations.
            One of 'min' or 'hour'

    Attributes
    ----------
    """
    def __init__(self, binance_api_key, binance_secret_key, lumnis_api_key, factors, coins, paper=True, time_frame="min"):

        self.trading_client  = TradingClient(binance_api_key, binance_secret_key, paper=paper)
        self.tradable_assets = self.get_tradable_assets(coins)
        self.time_frame      = time_frame
        self.lumnisfactors   = LumnisFactors(lumnis_api_key)
        self.factors         = factors

        self.tp              = 2
        self.sl              = 2
        self.ALPACA_TC       = 0.0015

        self.asset_meta_data = {}
        for asset in self.tradable_assets:
            self.asset_meta_data[asset.symbol] = {
                "min_order_size"     : asset.min_order_size,
                "current_ret"        : 0,
                "take_profit"        : 0,
                "stop_loss"          : 0,
                "stop_loss_percent"  : 0,
                "take_profit_percent": 0,
                "price_at_excexution": 0,
                "qty"                : 0,
                "side"               : 0
            }

        start_time           = time.time()
        self.history         = self.warmup_history()
        self.price_history   = self.warmup_price_history()
        print("Warmup time: ", (time.time() - start_time) /60, "min")

        self.update_history()
        print("Update time: ", (time.time() - start_time) /60, "min")

        self.MINUTE_CONDITION    = lambda interval, last_min : datetime.now().second  == interval and datetime.now().minute != last_min
    

    def run(self):
        """Runs the live trader.

        Parameters
        ----------
            None

        Returns
        -------
            None

        """
        last_min = datetime.now().minute
        while True:
            ## TODO: check if minutes is 00
            if self.MINUTE_CONDITION(0, last_min):
            
                account      = self.trading_client.get_account()
                account_cash = float( account.cash ) * 0.9
                cash_asset   = int( account_cash / len(self.tradable_assets) )
                side         = OrderSide.BUY

                try:
                    self.update_history(50)
                except:
                    print("Error updating history")
                    continue
                
                for asset in self.tradable_assets:
                    symbol      = asset.symbol
                    qty         = cash_asset / self.price_history[symbol].iloc[-1].close 
                    pos         = self.get_open_position(symbol)
                    if pos: continue

                    signal    = self.get_signal(symbol)
        
                    if self.asset_meta_data[asset.symbol]['min_order_size'] > qty:    continue 

                    vol       = getDailyVol(self.price_history[symbol]['close'], 60)
                    vol       = vol.iloc[-1]
                    vol       = min( vol, 0.1)
                    vol       = max(vol, self.ALPACA_TC*15)

                    close     = self.price_history[symbol].iloc[-1].close

                    if signal == 1:
                        take_profit = close + (self.tp * vol * close) 
                        stop_loss   = close - (self.sl * vol * close) 

                        order = self.submit_order(symbol, qty, side, take_profit=take_profit, stop_loss=stop_loss)
                        # print(order)
                        print("BUY ", symbol, " at ", close, " with ", qty, " shares", " TP: ", take_profit, " SL: ", stop_loss)

                last_min = datetime.now().minute

    def get_signal(self, symbol, strat='macd'):
        """Get trading signal. The core of the strategy.

        Parameters
        ----------
            symbol : str
                The symbol used in this signal

            strat : str, default='macd'
                The strategy to use

        Returns
        -------
        
        """
        raw_df  = self.history[symbol]
        df      = standardize(raw_df, 10000)

        if strat == 'macd':
            signal = (df['vpin_500'].iloc[-1] > 2) * 1
            return signal

        return 0

    def get_tradable_assets(self, coins):
        """Get tradable assets from Alpaca API.

        Parameters
        ----------
            coins : list
                Cryptocurrencies we want to trade

        Returns
        -------
            tradable_assets : list
                The assets to be traded in the strategy
        
        """
        all_assets_alpaca = self.get_all_assets()
        tradable_assets   = []

        for asset in all_assets_alpaca:
            for coin in coins:
                if coin == asset.symbol:
                    tradable_assets.append(asset)
        return tradable_assets
    
    def get_all_assets(self):        
        """Gets all assets from Alpaca.

        Parameters
        ----------
            None

        Returns
        -------
            (unnamed) : TradingClient
                Alpaca trading client containing all assets
        
        """
        search_params     = GetAssetsRequest(asset_class=AssetClass.CRYPTO)
        return self.trading_client.get_all_assets(search_params)
    
    def get_account(self):
        """Gets Alpaca trading account.

        Parameters
        ----------
            None

        Returns
        -------
            (unnnamed) : TradingClient.get_account()
                Alpaca trading client's account
        
        """
        return self.trading_client.get_account()

    def get_all_positions(self):
        """Gets all positions from Alpaca.

        Parameters
        ----------
            None

        Returns
        -------
            (unnamed) : TradingClient.get_all_positions()
                Alpaca trading client's positions
        
        """
        return self.trading_client.get_all_positions()

    def submit_order(self, symbol, qty, side, take_profit=None, stop_loss=None):
        """Submits an order to the Alpaca API.

        Parameters
        ----------
            symbol : str
                The symbol to submit an order for

            qty : float
                The quantity to submit the order for (in number of shares)

            side : OrderSide
                Either OrderSide.BUY or OrderSide.SELL

            take_profit : float, default=None
                Profit taking limit

            stop_loss : float, default=None
                Stop loss limit

        Returns
        -------
            (unnamed) : TradingClient.submit_order()
                Submitted order if successful
            False if unsuccessful
        
        """
        market_order_data = MarketOrderRequest(
            symbol=symbol, 
            qty=qty, 
            side=side, 
            time_in_force='gtc', 
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss)
            )
        try:
            order = self.trading_client.submit_order(order_data=market_order_data)

            # self.asset_meta_data[symbol]['invested'] = True
            # self.asset_meta_data[symbol]['price_at_excexution'] = close
            # self.asset_meta_data[symbol]['qty'] = qty
            # self.asset_meta_data[symbol]['side'] = side
            # self.asset_meta_data[symbol]['take_profit'] = take_profit
            # self.asset_meta_data[symbol]['stop_loss'] = stop_loss

            return order
        except Exception as e:
            print(e)
            return False
    
    def get_open_position(self, symbol):
        """Get an open position for a given symbol.

        Parameters
        ----------
            symbol : str
                Symbol to get open position for.

        Returns
        -------
            (unnamed) : TradingClient.get_open_position()
                Open position if successful
            False if unsuccessful            
        
        """
        try:
            return self.trading_client.get_open_position(symbol.replace("/", ""))
        except Exception as e:
            return False

    def get_current_ret(self, coin):
        """Gets the current return for a various coin and positions

        Parameters
        ----------
            coin : str
                Cryptocurrency we want to get current return for

        Returns
        -------
            position.unrealized_plpc : float
                Current return if successful
            False if unsuccessful
        
        """
        position = self.get_open_position(coin)
        if position:
            return position.unrealized_plpc
        else:
            return False
        
    def close_position(self, symbol):
        """Closes position on given symbol.

        Parameters
        ----------
            symbol : str
                Symbol to close position on

        Returns
        -------
            (unnamed) : TradingClient.close_position()
                Closed position if successful
            False if unsuccessful
        
        """
        try:
            return self.trading_client.close_position(symbol.replace("/", ""))
        except Exception as e:
            print(e)
            return False
    
    def close_all_positions(self):
        """Closes all open positions.

        Parameters
        ----------
            None

        Returns
        -------
            None
        
        """
        positions = self.get_all_positions()
        for pos in positions:
            try:
                self.close_position(pos.symbol)
            except Exception as e:
                print(e)

    def warmup_history(self):
        """Warmup the history for each tradable asset's factors.

        Parameters
        ----------
            None

        Returns
        -------
            history : pd.DataFrame
                Dataframe containing asset history
        
        """
        history = {}
        for asset in self.tradable_assets:
            data = []
            for factor in tqdm(self.factors):
                df_hist         = self.get_history(factor, asset)
                data.append(df_hist)
            history[asset.symbol] = pd.concat(data, axis=1).fillna(method='ffill')

        return history

    def warmup_price_history(self):
        """Warmup the price history for each tradable asset.

        Parameters
        ----------
            None

        Returns
        -------
            history : pd.DataFrame
                Dataframe containing price history
        
        """
        history = {}
        for asset in self.tradable_assets:
            df_hist         = self.get_history('price', asset)
            history[asset.symbol] = df_hist.fillna(method='ffill')
        return history
    
    def get_history(self, factor, asset):
        """Gets price history for an asset and factor

        Parameters
        ----------
            factor : str
                Factor to get history of

            asset : str
                Asset to get history of

        Returns
        -------
            df_hist : pd.DataFrame
                Dataframe containing history for asset and factor
        
        """
        today = pd.to_datetime("today") - pd.Timedelta(days=1.5)
        start = today - pd.Timedelta(days=80)
        
        today = today.strftime("%Y-%m-%d")
        start = start.strftime("%Y-%m-%d")

        df_hist         = self.lumnisfactors.get_historical_data(factor, "binance", asset.symbol.replace("/", ""),  self.time_frame, start, today)
        df_hist.index   = pd.to_datetime(df_hist.index, utc=True)
        df_hist         = df_hist[~df_hist.index.duplicated(keep='first')]

        return df_hist
    
    def update_history(self, lookback=2880):
        """

        Parameters
        ----------
            lookback : int, default=2880
                Lookback period in minutes, where default is two days

        Returns
        -------
            None
        
        """
        for asset in self.tradable_assets:
            symbol                            = asset.symbol.replace("/", "")

            df_live                           = self.lumnisfactors.get_multifactor_live_data(self.factors, "binance", symbol, self.time_frame, lookback)
            df_live.index                     = pd.to_datetime(df_live.index, utc=True)

            self.history[asset.symbol]        = pd.concat([self.history[asset.symbol], df_live], axis=0)
            self.history[asset.symbol]        = self.history[asset.symbol][~self.history[asset.symbol].index.duplicated(keep='first')].sort_index()
                    
            df_live                           = self.lumnisfactors.get_live_data('price', "binance", symbol, self.time_frame, lookback)
            df_live.index                     = pd.to_datetime(df_live.index, utc=True)
        
            self.price_history[asset.symbol]  = pd.concat([self.price_history[asset.symbol], df_live], axis=0)
            self.price_history[asset.symbol]  = self.price_history[asset.symbol][~self.price_history[asset.symbol].index.duplicated(keep='first')].sort_index()
