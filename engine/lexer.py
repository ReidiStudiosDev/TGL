from dataclasses import dataclass
from .errors import TGLSyntaxError
@dataclass
class Token:
    kind:str; value:str; line:int; column:int

def lex(source, filename='<memory>'):
    out=[]; i=0; line=1; col=1
    while i<len(source):
        c=source[i]
        if c.isspace():
            if c=='\n':line+=1;col=1
            else:col+=1
            i+=1;continue
        if source.startswith('//',i):
            j=source.find('\\\\',i+2); j=len(source) if j<0 else j+2
            chunk=source[i:j]; lines=chunk.split('\n'); line+=len(lines)-1; col=len(lines[-1])+1 if len(lines)>1 else col+len(chunk); i=j;continue
        sl,sc=line,col
        if c in ('"',"'"):
            q=c;j=i+1;esc=False
            while j<len(source):
                if source[j]==q and not esc:break
                if source[j]=='\\' and not esc:esc=True
                else:esc=False
                j+=1
            if j>=len(source):raise TGLSyntaxError('Cadena sin cerrar',filename,sl,sc)
            out.append(Token('STRING',source[i+1:j],sl,sc));chunk=source[i:j+1]
        elif c.isdigit():
            j=i+1
            while j<len(source) and (source[j].isdigit() or source[j] in '.smhd'):j+=1
            out.append(Token('NUMBER',source[i:j],sl,sc));chunk=source[i:j]
        elif c.isalpha() or c=='_':
            j=i+1
            while j<len(source) and (source[j].isalnum() or source[j]=='_'):j+=1
            out.append(Token('NAME',source[i:j],sl,sc));chunk=source[i:j]
        else:
            op=next((x for x in ('>=','<=','!=','==') if source.startswith(x,i)),c)
            if op not in '{}[](),;.:=+-*/<>':raise TGLSyntaxError(f'Carácter no reconocido: {c}',filename,sl,sc)
            out.append(Token(op,op,sl,sc));chunk=op;j=i+len(op)
        lines=chunk.split('\n');line+=len(lines)-1;col=len(lines[-1])+1 if len(lines)>1 else col+len(chunk);i=i+len(chunk)
    out.append(Token('EOF','',line,col));return out
