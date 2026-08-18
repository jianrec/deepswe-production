The cryptography examples currently implement each alphabet and modular rule locally. Caesar builds its own character map, Hill hard-codes uppercase English conversion and only supports encryption, and callers have no reusable way to validate alphabets or calculate modular inverses. This makes otherwise simple cipher round trips inconsistent and leaves the advertised Hill decrypt function as a stub.

Add a reusable classical-cipher foundation. Introduce an alphabet codec that validates a non-empty alphabet with unique symbols, converts symbols to numeric indices, reconstructs symbols while preserving configured case, and can either preserve or reject characters outside the alphabet. Introduce normalized modular arithmetic helpers for negative operands, the extended Euclidean algorithm, greatest-common-divisor checks, and modular inverses with explicit errors for non-invertible values. Extend Matrix with square-matrix validation, minors, determinant, cofactors, adjugate, and modular inverse operations. Matrix modular inversion must work for any square integer matrix whose determinant is coprime with the modulus, normalize every output cell into `[0, modulus)`, reject ragged/non-square matrices, and reject singular or non-invertible keys without mutating inputs.

Rework Hill encryption around these shared primitives and implement `hillCipherDecrypt`. Both directions accept an optional alphabet (default uppercase English), process messages in key-sized blocks, validate that the key length is a perfect square, require every message/key symbol to belong to the alphabet, and require an invertible key matrix. Encryption may pad an incomplete final block with the alphabet's first symbol; decryption must remove only caller-specified padding via an option rather than silently stripping legitimate characters. Preserve the existing two-argument uppercase behavior for valid one-block calls.

Refactor Caesar to use the same alphabet codec and normalized shifts while preserving its current default lowercase output and pass-through behavior for non-alphabet characters. Add named extended-Euclidean exports without breaking the existing default GCD export, make least-common-multiple safe for integer zero and signed inputs, and normalize polynomial-hash modular subtraction so rolling hashes never expose negative residues. Rail Fence must reject non-integer rail counts below two consistently in encode and decode instead of recursing into invalid states. Existing public exports remain available. All operations must be deterministic, avoid input mutation, and use ordinary JavaScript numbers with safe-integer validation where modular multiplication could otherwise become ambiguous.

Public API contract:
- `src/algorithms/math/modular-arithmetic/modularArithmetic.js`: named exports `mod(value, modulus)`, `extendedEuclidean(a, b)`, and `modularInverse(value, modulus)`.
- `src/algorithms/cryptography/alphabet/AlphabetCodec.js`: default class `AlphabetCodec` with constructor `(alphabet, options = {})`, `encode(text)`, `decode(indices)`, `indexOf(symbol)`, and `symbolAt(index)`.
- `src/algorithms/math/matrix/Matrix.js`: named exports `minor(matrix, row, column)`, `determinant(matrix)`, `adjugate(matrix)`, and `inverseMod(matrix, modulus)`.
- `src/algorithms/cryptography/hill-cipher/hillCipher.js`: `hillCipherEncrypt(message, keyString, options = {})` and `hillCipherDecrypt(cipherText, keyString, options = {})`; options support `alphabet`, `padding`, and `stripPadding`.
- Existing `caesarCipherEncrypt(str, shift, alphabet)` and `caesarCipherDecrypt(str, shift, alphabet)` signatures and default behavior remain compatible.
- Existing default `euclideanAlgorithm(a, b)` and `leastCommonMultiple(a, b)` exports remain compatible for prior valid inputs.

Acceptance criteria:
- `mod`, `extendedEuclidean`, and `modularInverse` normalize negative operands and reject unsafe, zero-modulus, or non-invertible inputs with stable errors.
- Matrix determinant, adjugate, and modular inverse support square integer matrices of multiple sizes without mutating the source matrix.
- `hillCipherDecrypt(hillCipherEncrypt(message, key, options), key, options)` returns the normalized original message for invertible keys and supported alphabets.
- Hill cipher supports multi-block messages and deterministic opt-in padding for final partial blocks.
- Hill cipher rejects malformed key lengths, symbols outside the alphabet, and matrices whose determinant has no inverse modulo the alphabet size.
- Caesar keeps its existing default lowercase and pass-through behavior while correctly normalizing arbitrarily large positive and negative shifts.
- Rail Fence encode and decode reject rail counts that are non-integers or less than two.
- Polynomial rolling hashes remain in the configured non-negative modulus range after deletion and re-append operations.
- Existing tests pass unchanged and all newly exported helpers have deterministic, side-effect-free behavior.
