import os
import sys
import pickle
import numpy as np
import json
from datetime import datetime
import base64
import io
import uuid
import re
import time

# CONFIGURAÇÃO CRÍTICA
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
os.environ['OPENCV_LOG_LEVEL'] = 'ERROR'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
# ALTERAÇÃO: Mantive o backend do OpenCV que é mais leve.
os.environ['DEEPFACE_BACKEND'] = 'opencv'

print(f"🚀 Python version: {sys.version}")
print(f"📦 NumPy version: {np.__version__}")

try:
    import pickle5 as pickle
    print("✅ Using pickle5 for better compatibility")
except ImportError:
    import pickle
    print("⚠️ Using standard pickle")

# Verificar importações críticas
try:
    from flask import Flask, render_template, request, jsonify
    print("✅ Flask importado")
except ImportError as e:
    print(f"❌ Flask não disponível: {e}")
    sys.exit(1)

try:
    from PIL import Image
    print("✅ Pillow importado")
except ImportError as e:
    print(f"❌ Pillow não disponível: {e}")
    sys.exit(1)

try:
    import cv2
    CV2_AVAILABLE = True
    print("✅ OpenCV importado com sucesso")
except ImportError as e:
    print(f"❌ OpenCV não disponível: {e}")
    CV2_AVAILABLE = False

try:
    import psycopg2
    print("✅ psycopg2 importado")
except ImportError as e:
    print(f"❌ psycopg2 não disponível: {e}")
    sys.exit(1)

# DeepFace - Carregamento condicional mais robusto
DEEPFACE_AVAILABLE = False
DeepFace = None

if CV2_AVAILABLE:
    try:
        print("🔄 Tentando importar DeepFace...")
        from deepface import DeepFace
        DEEPFACE_AVAILABLE = True
        print("✅ DeepFace importado com sucesso. O modelo será carregado no primeiro uso.")
        # ALTERAÇÃO: O teste de verificação que dependia da internet foi REMOVIDO.
        # Ele era a causa principal do problema, pois falhava e desativava o DeepFace.
    except ImportError as e:
        print(f"❌ DeepFace não pôde ser importado: {e}")
    except Exception as e:
        print(f"⚠️ DeepFace importado, mas pode haver problemas: {e}")
else:
    print("❌ OpenCV não disponível - DeepFace não funcionará")

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.float32, np.float64, np.float16)):
            return float(obj)
        elif isinstance(obj, (np.int32, np.int64, np.int16, np.int8)):
            return int(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.bool_):
            return bool(obj)
        return super().default(obj)

app = Flask(__name__)
app.json_encoder = NumpyEncoder

UPLOAD_FOLDER = "static/uploads"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-123')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

def clean_database_url(url):
    if not url: return url
    url = re.sub(r'^psql\s*[\'"]?', '', url)
    url = re.sub(r'[\'"]\s*$', '', url)
    url = re.sub(r'[&?]channel_binding=require', '', url)
    return url.strip()

def get_db_connection():
    try:
        DATABASE_URL = os.getenv('DATABASE_URL')
        if not DATABASE_URL: raise ValueError("DATABASE_URL não encontrada")
        DATABASE_URL = clean_database_url(DATABASE_URL)
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Erro na conexão com o banco: {e}")
        return None

def init_database():
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pessoas (
                    id SERIAL PRIMARY KEY, nome VARCHAR(255) NOT NULL, email VARCHAR(255),
                    telefone VARCHAR(50), embedding BYTEA,
                    data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ativo BOOLEAN DEFAULT true
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS registros_reconhecimento (
                    id SERIAL PRIMARY KEY, pessoa_id INTEGER REFERENCES pessoas(id),
                    metodo VARCHAR(50), confianca DECIMAL(5,2),
                    data_reconhecimento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            cursor.close()
            conn.close()
            print("✅ Banco de dados inicializado com sucesso!")
    except Exception as e:
        print(f"⚠️ Aviso ao inicializar banco: {e}")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def base64_to_image(base64_string):
    try:
        if ',' in base64_string:
            base64_string = base64_string.split(',')[1]
        img_data = base64.b64decode(base64_string)
        return Image.open(io.BytesIO(img_data))
    except Exception as e:
        raise ValueError(f"Erro ao converter imagem: {str(e)}")

def safe_pickle_loads(data):
    try:
        return pickle.loads(data)
    except Exception:
        try:
            return pickle.loads(data, encoding='latin1')
        except Exception:
            try:
                return pickle.loads(data, errors='ignore')
            except Exception as e:
                print(f"❌ Erro final ao carregar pickle: {e}")
                return None

def extract_embedding_optimized(image_path):
    if not DEEPFACE_AVAILABLE:
        print("❌ DeepFace não está disponível para extrair embedding.")
        return None
    try:
        print("🔄 Extraindo embedding facial com DeepFace...")
        embedding_objs = DeepFace.represent(
            img_path=image_path, model_name="Facenet", detector_backend="opencv",
            enforce_detection=True, align=True, normalization="base"
        )
        if embedding_objs and len(embedding_objs) > 0:
            embedding_array = np.array(embedding_objs[0]['embedding'], dtype=np.float32)
            norm = np.linalg.norm(embedding_array)
            if norm > 0: embedding_array /= norm
            print(f"📊 Embedding extraído: shape {embedding_array.shape}, norma: {np.linalg.norm(embedding_array):.4f}")
            return pickle.dumps(embedding_array, protocol=4)
        else:
            print("❌ Nenhum rosto detectado na imagem pelo DeepFace.")
            return None
    except Exception as e:
        print(f"❌ Erro crítico no DeepFace durante a extração: {e}")
        return None

# ALTERAÇÃO: A função de fallback agora retorna um erro claro, em vez de um resultado falso.
def emergency_fallback(reason=""):
    """Fallback de emergência que retorna uma mensagem de erro clara."""
    print(f"🚨 Acionando fallback de emergência. Razão: {reason}")
    return {
        "error": "O serviço de reconhecimento facial não está disponível no momento. Verifique os logs do servidor.",
        "deepface_available": DEEPFACE_AVAILABLE
    }

def facial_recognition_from_embedding(image_path):
    """Reconhecimento facial real, comparando com o banco de dados."""
    if not DEEPFACE_AVAILABLE:
        return emergency_fallback("DeepFace não foi inicializado.")

    try:
        input_embedding_data = extract_embedding_optimized(image_path)
        if input_embedding_data is None:
            return {"success": False, "message": "Não foi possível detectar um rosto na imagem enviada."}

        input_array = safe_pickle_loads(input_embedding_data)
        if input_array is None:
            return {"error": "Erro ao processar as características faciais da imagem de entrada."}

        conn = get_db_connection()
        if not conn: return {"error": "Erro de conexão com o banco de dados."}

        cursor = conn.cursor()
        cursor.execute('SELECT id, nome, email, telefone, embedding FROM pessoas WHERE ativo = true AND embedding IS NOT NULL')
        pessoas = cursor.fetchall()
        conn.close()

        if not pessoas:
            return {"success": False, "message": "Nenhuma pessoa com registro facial encontrada no sistema."}

        print(f"🔍 Comparando com {len(pessoas)} pessoas no banco...")
        best_match = None
        best_confidence = 0.0
        threshold = 60.0 # ALTERAÇÃO: Aumentei um pouco o threshold para evitar falsos positivos. Ajuste se necessário.

        for pessoa_id, nome, email, telefone, db_embedding_data in pessoas:
            if not db_embedding_data: continue
            
            try:
                db_array = safe_pickle_loads(db_embedding_data)
                if db_array is None:
                    print(f"⚠️ Embedding corrompido para a pessoa {nome} (ID: {pessoa_id}), pulando.")
                    continue
                
                similarity = cosine_similarity(input_array, db_array)
                confidence = similarity * 100

                print(f"   👤 Comparando com {nome}: {confidence:.2f}% de similaridade")

                if confidence > best_confidence:
                    best_confidence = confidence
                    if confidence > threshold:
                        best_match = {'id': pessoa_id, 'nome': nome, 'email': email, 'telefone': telefone}
            
            except Exception as e:
                print(f"⚠️ Erro ao comparar com {nome}: {e}")

        if best_match:
            print(f"✅ PESSOA IDENTIFICADA: {best_match['nome']} com {best_confidence:.2f}% de confiança")
            return {"success": True, "person": best_match, "confidence": float(best_confidence)}
        else:
            print(f"❌ Nenhuma correspondência encontrada acima do threshold de {threshold}%. Maior confiança foi {best_confidence:.2f}%.")
            return {"success": False, "message": "Pessoa não identificada na base de dados."}

    except Exception as e:
        print(f"❌ Erro inesperado durante o reconhecimento facial: {e}")
        return emergency_fallback(f"Exceção: {e}")

def cosine_similarity(a, b):
    try:
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0: return 0.0
        return max(0.0, dot_product / (norm_a * norm_b))
    except Exception as e:
        print(f"❌ Erro no cálculo de similaridade: {e}")
        return 0.0

def save_recognition_log(person_id, metodo, confianca):
    try:
        confianca_python = float(confianca)
        conn = get_db_connection()
        if not conn: return
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO registros_reconhecimento (pessoa_id, metodo, confianca) VALUES (%s, %s, %s)',
            (person_id, metodo, confianca_python)
        )
        conn.commit()
        conn.close()
        print(f"📝 Log salvo: pessoa_id={person_id}, método={metodo}, confiança={confianca_python}%")
    except Exception as e:
        print(f"❌ Erro ao salvar log: {e}")

# --- ROTAS E APIs (sem grandes alterações, apenas chamando as funções corrigidas) ---

@app.route('/')
def index(): return render_template('index.html')

@app.route('/cadastro')
def cadastro(): return render_template('cadastro.html')

@app.route('/pessoas')
def pessoas():
    # ... (código original mantido)
    try:
        conn = get_db_connection()
        if not conn:
            return render_template('pessoas.html', pessoas=[], error="Erro de conexão com o banco")
            
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, email, telefone, data_cadastro 
            FROM pessoas 
            WHERE ativo = true 
            ORDER BY nome
        ''')
        
        pessoas_data = []
        for row in cursor.fetchall():
            pessoas_data.append({
                'id': row[0],
                'nome': row[1],
                'email': row[2],
                'telefone': row[3],
                'data_cadastro': row[4]
            })
        
        conn.close()
        
        return render_template('pessoas.html', pessoas=pessoas_data)
    except Exception as e:
        return render_template('pessoas.html', pessoas=[], error=str(e))


@app.route('/estatisticas')
def estatisticas(): return render_template('estatisticas.html')

@app.route('/api/estatisticas', methods=['GET'])
def api_estatisticas():
    # ... (código original mantido)
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Erro de conexão com o banco"})
            
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM pessoas WHERE ativo = true')
        total_pessoas = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM registros_reconhecimento')
        total_reconhecimentos = cursor.fetchone()[0]
        
        cursor.execute('SELECT metodo, COUNT(*) FROM registros_reconhecimento GROUP BY metodo')
        metodo_data = cursor.fetchall()
        reconhecimentos_metodo = {metodo: count for metodo, count in metodo_data}
        
        conn.close()
        
        return jsonify({
            "total_pessoas": total_pessoas,
            "total_reconhecimentos": total_reconhecimentos,
            "reconhecimentos_metodo": reconhecimentos_metodo,
            "reconhecimentos_7_dias": {}
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/cadastrar_pessoa', methods=['POST'])
def cadastrar_pessoa():
    try:
        nome = request.form.get('nome', '').strip()
        if not nome: return jsonify({"error": "Nome é obrigatório"})
        if 'foto' not in request.files: return jsonify({"error": "Foto é obrigatória"})
        file = request.files['foto']
        if not file or not allowed_file(file.filename):
            return jsonify({"error": "Arquivo inválido ou não selecionado."})

        image = Image.open(file.stream)
        image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        
        temp_filename = f"temp_{uuid.uuid4().hex}.jpg"
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        image.save(temp_path, 'JPEG', quality=85)

        embedding = extract_embedding_optimized(temp_path)
        os.remove(temp_path)

        if embedding is None:
            return jsonify({"error": "Não foi possível detectar um rosto na foto para o cadastro. Tente uma imagem mais nítida e com o rosto bem visível."})

        conn = get_db_connection()
        if not conn: return jsonify({"error": "Erro de conexão com o banco"})
        
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO pessoas (nome, email, telefone, embedding) VALUES (%s, %s, %s, %s) RETURNING id',
            (nome, request.form.get('email', ''), request.form.get('telefone', ''), embedding)
        )
        pessoa_id = cursor.fetchone()[0]
        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": f"Pessoa {nome} cadastrada com sucesso!", "pessoa_id": pessoa_id})

    except Exception as e:
        print(f"❌ Erro no cadastro: {e}")
        return jsonify({"error": f"Erro inesperado no cadastro: {str(e)}"})


@app.route('/api/recognize_upload', methods=['POST'])
def recognize_upload():
    try:
        if 'file' not in request.files: return jsonify({"error": "Nenhum arquivo enviado"})
        file = request.files['file']
        if not file or not allowed_file(file.filename): return jsonify({"error": "Arquivo inválido ou não selecionado"})

        image = Image.open(file.stream)
        image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(filepath, 'JPEG', quality=85)

        result = facial_recognition_from_embedding(filepath)
        os.remove(filepath)
        
        if result.get('success'):
            save_recognition_log(result['person']['id'], 'upload', result['confidence'])
            
        return jsonify(result)

    except Exception as e:
        print(f"❌ Erro no processamento do upload: {str(e)}")
        return jsonify(emergency_fallback(f"Exceção no upload: {e}"))

@app.route('/api/recognize_camera', methods=['POST'])
def recognize_camera():
    try:
        data = request.get_json()
        if not data or 'image' not in data: return jsonify({"error": "Nenhuma imagem recebida"})

        image = base64_to_image(data['image'])
        image.thumbnail((400, 400), Image.Resampling.LANCZOS)
        
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(filepath, 'JPEG', quality=85)

        result = facial_recognition_from_embedding(filepath)
        os.remove(filepath)
        
        if result.get('success'):
            save_recognition_log(result['person']['id'], 'camera', result['confidence'])

        return jsonify(result)

    except Exception as e:
        print(f"❌ Erro no processamento da câmera: {str(e)}")
        return jsonify(emergency_fallback(f"Exceção na câmera: {e}"))

# ... (Restante das APIs /health, /api/test, etc. podem ser mantidas como estão)
@app.route('/api/pessoas', methods=['GET'])
def api_pessoas():
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify([])
            
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, nome, email, telefone, data_cadastro 
            FROM pessoas 
            WHERE ativo = true 
            ORDER BY nome
        ''')
        
        pessoas = []
        for row in cursor.fetchall():
            pessoas.append({
                'id': row[0],
                'nome': row[1],
                'email': row[2],
                'telefone': row[3],
                'data_cadastro': row[4].strftime('%Y-%m-%d %H:%M:%S')
            })
        
        conn.close()
        return jsonify(pessoas)
        
    except Exception as e:
        return jsonify([])

@app.route('/api/deletar_pessoa/<int:pessoa_id>', methods=['DELETE'])
def deletar_pessoa(pessoa_id):
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"error": "Erro de conexão com o banco"})
            
        cursor = conn.cursor()
        cursor.execute('UPDATE pessoas SET ativo = false WHERE id = %s', (pessoa_id,))
        conn.commit()
        conn.close()
        
        return jsonify({"success": True, "message": "Pessoa removida com sucesso"})
        
    except Exception as e:
        return jsonify({"error": f"Erro ao remover pessoa: {str(e)}"})

@app.route('/health')
def health_check():
    db_status = "connected" if get_db_connection() else "disconnected"
    return jsonify({
        "status": "healthy", "timestamp": datetime.now().isoformat(),
        "database": db_status, "deepface_available": DEEPFACE_AVAILABLE,
        "opencv_available": CV2_AVAILABLE, "python_version": sys.version.split()[0],
    })
# --- Fim das rotas ---

if __name__ == '__main__':
    print("🚀 Iniciando Face Confirmation System...")
    print(f"✅ DeepFace disponível para uso: {DEEPFACE_AVAILABLE}")
    print(f"✅ OpenCV disponível: {CV2_AVAILABLE}")
    
    init_database()
    
    port = int(os.environ.get('PORT', 8080))
    # Para produção, o ideal é usar um servidor WSGI como Gunicorn, não o de desenvolvimento do Flask.
    # O código original para Gunicorn está bom.
    print(f"📡 Servidor pronto para iniciar na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)