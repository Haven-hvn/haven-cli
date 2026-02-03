"""Standard exit codes for Haven CLI.

This module defines standard exit codes used across the Haven CLI
for consistent error reporting and scripting support.
"""


class ExitCode:
    """Standard exit codes for Haven CLI.
    
    These codes follow common Unix conventions where possible:
    - 0: Success
    - 1: General error
    - 2: Misuse of command (bash builtin)
    - 126: Command invoked cannot execute
    - 127: Command not found
    - 128+N: Fatal error signal N
    - 130: Script terminated by Ctrl+C (SIGINT)
    
    Haven-specific codes start at 2:
    - 2: Configuration error
    - 3: Plugin error
    - 4: Pipeline error
    - 5: Network error
    - 6: Storage error
    - 7: Invalid argument
    - 8: Not found
    - 9: Permission denied
    """
    
    # Standard success
    SUCCESS = 0
    
    # General errors
    GENERAL_ERROR = 1
    
    # Haven-specific errors (2-9)
    CONFIGURATION_ERROR = 2
    PLUGIN_ERROR = 3
    PIPELINE_ERROR = 4
    NETWORK_ERROR = 5
    STORAGE_ERROR = 6
    INVALID_ARGUMENT = 7
    NOT_FOUND = 8
    PERMISSION_DENIED = 9
    
    # Signal-based exits (128 + signal number)
    CANCELLED = 130  # Ctrl+C (SIGINT = 2)
    
    @classmethod
    def get_name(cls, code: int) -> str:
        """Get the name of an exit code.
        
        Args:
            code: The exit code value
            
        Returns:
            Human-readable name for the exit code
        """
        names = {
            cls.SUCCESS: "SUCCESS",
            cls.GENERAL_ERROR: "GENERAL_ERROR",
            cls.CONFIGURATION_ERROR: "CONFIGURATION_ERROR",
            cls.PLUGIN_ERROR: "PLUGIN_ERROR",
            cls.PIPELINE_ERROR: "PIPELINE_ERROR",
            cls.NETWORK_ERROR: "NETWORK_ERROR",
            cls.STORAGE_ERROR: "STORAGE_ERROR",
            cls.INVALID_ARGUMENT: "INVALID_ARGUMENT",
            cls.NOT_FOUND: "NOT_FOUND",
            cls.PERMISSION_DENIED: "PERMISSION_DENIED",
            cls.CANCELLED: "CANCELLED",
        }
        return names.get(code, f"UNKNOWN({code})")
    
    @classmethod
    def get_description(cls, code: int) -> str:
        """Get the description of an exit code.
        
        Args:
            code: The exit code value
            
        Returns:
            Human-readable description for the exit code
        """
        descriptions = {
            cls.SUCCESS: "Operation completed successfully",
            cls.GENERAL_ERROR: "An unexpected error occurred",
            cls.CONFIGURATION_ERROR: "Configuration error or invalid config file",
            cls.PLUGIN_ERROR: "Plugin loading or execution error",
            cls.PIPELINE_ERROR: "Pipeline processing error",
            cls.NETWORK_ERROR: "Network or connectivity error",
            cls.STORAGE_ERROR: "Storage or Filecoin operation error",
            cls.INVALID_ARGUMENT: "Invalid command-line argument",
            cls.NOT_FOUND: "Requested resource not found",
            cls.PERMISSION_DENIED: "Permission denied",
            cls.CANCELLED: "Operation cancelled by user",
        }
        return descriptions.get(code, f"Unknown exit code: {code}")
