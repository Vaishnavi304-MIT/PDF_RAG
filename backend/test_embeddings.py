
import numpy as np
from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"

print("Loading FastEmbed model...")

model = TextEmbedding(model_name=MODEL_NAME)

documents = [
    "IntelliDoc AI is a PDF chatbot that answers questions.",
    "Retrieval augmented generation retrieves relevant document chunks.",
    "Python is used to build backend applications."
]

query = [
    "What is IntelliDoc AI?"
]

print("\nGenerating document embeddings...")

doc_vectors = np.asarray(
    list(model.embed(documents)),
    dtype="float32"
)

query_vectors = np.asarray(
    list(model.embed(query)),
    dtype="float32"
)

print("Document shape:", doc_vectors.shape)
print("Query shape:", query_vectors.shape)

assert doc_vectors.shape == (3, 384)
assert query_vectors.shape == (1, 384)

# Normalize vectors
doc_vectors /= np.maximum(
    np.linalg.norm(doc_vectors, axis=1, keepdims=True),
    1e-12
)

query_vectors /= np.maximum(
    np.linalg.norm(query_vectors, axis=1, keepdims=True),
    1e-12
)

similarities = doc_vectors @ query_vectors[0]

print("\nSimilarity scores:")

for text, score in zip(documents, similarities):
    print(f"{score:.4f} - {text}")

print("\nSUCCESS: FastEmbed is working!")
