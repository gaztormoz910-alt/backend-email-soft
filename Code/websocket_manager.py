# Refactored to use the central BackgroundParserEngine
# We alias parser_engine to ws_manager here to maintain seamless compatibility 
# with other modules (like email_extractor.cli.main) that expect ws_manager.

from engine import parser_engine as ws_manager
