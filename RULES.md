# Project Rules & Collaboration Guidelines

## 1. Human-in-the-Loop Execution
- **Pacing:** We move one phase at a time (as outlined in `PRD.md`).
- **AI Constraints:** The AI will write code and propose architectures, but will **stop** and wait for the human to test the code against real hardware or real data before moving to the next major component.
- **Learning:** The AI must explain the math and logic behind complex signal processing (e.g., IFFT, CIR) so the human researcher understands the core concepts for the paper.

## 2. Code Quality & Standards
- **Python (ML Pipeline):** 
  - Follow PEP-8.
  - Use Type Hints extensively for clarity.
  - Keep math operations vectorized using NumPy/PyTorch.
- **TypeScript/React (Dashboard):** 
  - Use functional components and hooks.
  - Strict typing.
  - UI components must strictly adhere to the minimal `shadcn/ui` aesthetic.

## 3. Data Privacy & Integrity
- As per the original AERIS design, participant names and personally identifiable information must **never** be committed or exposed in processed ML datasets.
- Ensure all new data collection schemas respect the anonymity rules.

## 4. Documentation
- Update `implementation_plan.md` as decisions evolve.
- Heavily comment the complex signal processing functions (the "Wow Factor" code) as this will be the basis of the research paper methodology.

