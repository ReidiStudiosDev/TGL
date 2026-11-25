class TGLError(Exception):
    pass

class TGLSyntaxError(TGLError):
    def __init__(self, message, filename='<memory>', line=1, column=1):
        self.message, self.filename, self.line, self.column = message, str(filename), line, column
        super().__init__(f"{self.filename}:{line}:{column}: {message}")

class TGLRuntimeError(TGLError):
    pass
