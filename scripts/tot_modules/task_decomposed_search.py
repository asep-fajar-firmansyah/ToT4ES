#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Task-Decomposed Tree Search
Implements the architecture with separate task-specific thought generators
"""

from collections import deque
from typing import Any, Callable, List, Dict, Optional, Tuple
import time

from .tree_node import TreeNode
from .llm_wrapper import Llama32Chat
from .utils import extract_first_int


class TaskDecomposedToT:
    """
    Task-Decomposed Tree-of-Thoughts for entity summarization.
    
    Uses three separate thought generators, one for each criterion:
    - Relatedness generator
    - Informativeness generator  
    - Diversity/Coverage generator
    
    This implements the architecture shown in the diagram.
    """

    def __init__(
        self,
        llm: Llama32Chat,
        input_seq: str,
        get_relatedness_prompt: Callable[[str, str], str],
        get_informativeness_prompt: Callable[[str, str], str],
        get_diversity_prompt: Callable[[str, str], str],
        get_state_eval_prompt: Callable[[str, List[str]], str],
        heuristic_calculator: Callable[[List[str], List[str]], List[float]],
        num_triples: int,
        llm_relatedness: Optional[Llama32Chat] = None,
        llm_informativeness: Optional[Llama32Chat] = None,
        llm_diversity: Optional[Llama32Chat] = None,
        llm_evaluation: Optional[Llama32Chat] = None,
        heuristic_scorer=None,
        w_relatedness: float = 0.4,
        w_informativeness: float = 0.4,
        w_coverage: float = 0.2,
        prune_keep_multiplier: float = 1.5,
        no_scoring_mode: Optional[str] = None,
    ):
        """
        Initialize Task-Decomposed ToT search.
        
        Args:
            llm: Default LLM wrapper instance (used if task-specific LLMs not provided)
            input_seq: Global description string
            get_relatedness_prompt: Prompt function for relatedness task
            get_informativeness_prompt: Prompt function for informativeness task
            get_diversity_prompt: Prompt function for diversity task
            get_state_eval_prompt: Prompt function for state evaluation
            heuristic_calculator: Function to aggregate evaluation scores
            num_triples: Total number of triples
            llm_relatedness: Optional LLM specifically for relatedness task
            llm_informativeness: Optional LLM specifically for informativeness task
            llm_diversity: Optional LLM specifically for diversity task
            llm_evaluation: Optional LLM specifically for evaluation task
        """
        self.llm = llm
        self.input_seq = input_seq
        self.root = TreeNode(state="", thought="")
        self.n_steps = 5
        self.n_candidates_per_task = 2  # Generate 2 candidates per task
        self.n_evals = 3
        self.heuristic_calculator = heuristic_calculator
        self.breadth_limit = 3
        self.thought_temperature = 0.8
        self.eval_temperature = 0.3
        self.do_sample: Optional[bool] = None
        
        # Task-specific prompt generators
        self.get_relatedness_prompt = get_relatedness_prompt
        self.get_informativeness_prompt = get_informativeness_prompt
        self.get_diversity_prompt = get_diversity_prompt
        self.get_state_eval_prompt = get_state_eval_prompt
        
        # Task-specific LLMs (fall back to default if not provided)
        self.llm_relatedness = llm_relatedness or llm
        self.llm_informativeness = llm_informativeness or llm
        self.llm_diversity = llm_diversity or llm
        self.llm_evaluation = llm_evaluation or llm
        
        # Heuristic scorer (for ablation studies without LLM evaluation)
        self.heuristic_scorer = heuristic_scorer
        self.use_heuristic_scoring = heuristic_scorer is not None
        
        # Semantic weights for value function
        self.w_relatedness = w_relatedness
        self.w_informativeness = w_informativeness
        self.w_coverage = w_coverage
        self.prune_keep_multiplier = max(1.0, prune_keep_multiplier)
        
        # State evaluation cache to avoid re-evaluating identical states
        self.eval_cache = {}

        # Optional ablation mode that bypasses state scoring entirely.
        if no_scoring_mode not in (None, "greedy"):
            raise ValueError(f"Unsupported no_scoring_mode: {no_scoring_mode}")
        self.no_scoring_mode = no_scoring_mode
        
        self.num_triples = num_triples

    def validate_state(self, state: str) -> bool:
        """Validate state has no duplicates and valid indices."""
        if not state.strip():
            return True
        
        try:
            ids = [int(x) for x in state.strip().splitlines() if x.strip().isdigit()]
            if len(ids) != len(set(ids)):
                return False
            if not all(1 <= i <= self.num_triples for i in ids):
                return False
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def canonical_state_key(state: str) -> Tuple[int, ...]:
        """Order-invariant identity for a state: the sorted set of triple IDs.

        Two states selecting the same triples are the same summary candidate
        regardless of the order they were added in, so this key is used to
        deduplicate branches across different parents and to reuse cached
        evaluations for combinations seen before.
        """
        if not state.strip():
            return tuple()
        return tuple(sorted(
            int(x) for x in state.strip().splitlines() if x.strip().isdigit()
        ))

    def chat_completions(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        n: int = 1,
        stop: Optional[List[str]] = None,
        llm: Optional[Llama32Chat] = None,
        do_sample: Optional[bool] = None,
        enable_thinking: bool = False,
    ) -> List[str]:
        """Query LLM with chat interface."""
        llm_to_use = llm or self.llm
        messages = [{"role": "user", "content": prompt}]

        # Build kwargs — only pass enable_thinking to models that support it (Qwen3)
        chat_kwargs = dict(
            messages=messages,
            temperature=temperature,
            max_new_tokens=max_tokens,
            n=n,
        )
        if do_sample is not None:
            chat_kwargs["do_sample"] = do_sample
        # Qwen3CoderChat accepts enable_thinking; others will ignore unknown kwargs
        import inspect
        if "enable_thinking" in inspect.signature(llm_to_use.chat).parameters:
            chat_kwargs["enable_thinking"] = enable_thinking

        raw_outputs = llm_to_use.chat(**chat_kwargs)
        outputs: List[str] = []
        for text in raw_outputs:
            text = text.lstrip()
            if stop:
                for s in stop:
                    if s:
                        idx = text.find(s)
                        if idx > 0:
                            text = text[:idx]
            outputs.append(text.strip())
        return outputs

    def task_thought_generator(
        self, 
        state: str, 
        task_prompt_fn: Callable[[str, str], str],
        task_name: str,
        llm: Optional[Llama32Chat] = None,
        verbose: bool = False,
    ) -> List[str]:
        """
        Generate thoughts using a specific task prompt.
        
        Args:
            state: Current state string
            task_prompt_fn: Task-specific prompt generator
            task_name: Name of task (for logging)
            llm: Optional task-specific LLM to use
            
        Returns:
            List of unique thought strings (triple indices)
        """
        prompt = task_prompt_fn(self.input_seq, state)
        raw_thoughts = self.chat_completions(
            prompt=prompt,
            temperature=self.thought_temperature,
            max_tokens=32,
            n=self.n_candidates_per_task,
            stop=["\n", ".", ","],
            llm=llm,
            do_sample=self.do_sample,
            enable_thinking=False,
        )

        thought_ids: List[str] = []
        for txt in raw_thoughts:
            tid = extract_first_int(txt)
            if tid is not None and 1 <= tid <= self.num_triples:
                thought_ids.append(str(tid))

        unique_thoughts = list(dict.fromkeys(thought_ids))

        # Fallback for models that occasionally return empty/non-numeric text
        # on the first sampled attempt (observed with some Llama variants).
        if not unique_thoughts:
            fallback_outputs = self.chat_completions(
                prompt=prompt,
                temperature=0.0,
                max_tokens=64,
                n=max(2, self.n_candidates_per_task),
                stop=None,
                llm=llm,
                do_sample=False,
                enable_thinking=False,
            )

            fallback_ids: List[str] = []
            for txt in fallback_outputs:
                tid = extract_first_int(txt)
                if tid is not None and 1 <= tid <= self.num_triples:
                    fallback_ids.append(str(tid))

            unique_thoughts = list(dict.fromkeys(fallback_ids))

            if verbose and unique_thoughts:
                print(f"    [fallback:{task_name}] recovered thoughts: {unique_thoughts}")

        return unique_thoughts

    def generate_thoughts_all_tasks(self, state: str, verbose: bool = False) -> Dict[str, List[str]]:
        """
        Generate thoughts from all three task-specific generators.
        
        OPTIMIZED: Uses batched generation when all tasks use same model.
        
        Args:
            state: Current state
            verbose: Whether to print debug info
            
        Returns:
            Dictionary mapping task name to thought list
        """
        tasks = {
            "relatedness": (self.get_relatedness_prompt, self.llm_relatedness),
            "informativeness": (self.get_informativeness_prompt, self.llm_informativeness),
            "diversity": (self.get_diversity_prompt, self.llm_diversity),
        }
        
        # Check if all tasks use the same model (for batching optimization)
        all_same_model = (
            self.llm_relatedness == self.llm and
            self.llm_informativeness == self.llm and
            self.llm_diversity == self.llm
        )
        
        all_thoughts = {}
        
        if all_same_model:
            # SHARED-MODEL PATH: generate thoughts with the same LLM for each task
            if verbose:
                print(f"  Generating thoughts for ALL TASKS (shared model)...")

            for task_name, (prompt_fn, task_llm) in tasks.items():
                thoughts = self.task_thought_generator(
                    state,
                    prompt_fn,
                    task_name,
                    llm=task_llm,
                    verbose=verbose,
                )
                all_thoughts[task_name] = thoughts

                if verbose:
                    print(f"    {task_name}: {thoughts}")
        else:
            # SLOW PATH: Sequential generation (different models per task)
            for task_name, (prompt_fn, task_llm) in tasks.items():
                if verbose:
                    print(f"  Generating thoughts for {task_name.upper()}...")
                    if task_llm != self.llm:
                        print(f"    Using task-specific model: {task_llm.model_id}")
                
                thoughts = self.task_thought_generator(
                    state,
                    prompt_fn,
                    task_name,
                    llm=task_llm,
                    verbose=verbose,
                )
                all_thoughts[task_name] = thoughts
                
                if verbose:
                    print(f"    {task_name}: {thoughts}")
        
        return all_thoughts

    def state_evaluator(self, states: List[str]) -> List[float]:
        """
        Evaluate states using vote-based aggregation with chunking and caching.
        
        - Caches results to avoid re-evaluating identical states
        - Chunks evaluation into smaller batches for faster inference
        - Returns scores in same order as input states
        """
        n_states = len(states)
        
        # Check cache for already-evaluated states. Keyed by the order-invariant
        # canonical form so combinations reached via a different parent/order
        # reuse the score instead of re-querying the LLM.
        scores = []
        uncached_indices = []
        uncached_states = []
        
        for i, state in enumerate(states):
            cache_key = self.canonical_state_key(state)
            if cache_key in self.eval_cache:
                scores.append(self.eval_cache[cache_key])
            else:
                scores.append(None)  # Placeholder
                uncached_indices.append(i)
                uncached_states.append(state)
        
        if uncached_states:
            # Evaluate uncached states in chunks (max 5 per chunk for faster inference)
            MAX_STATES_PER_CHUNK = 5
            chunk_scores = []
            
            for chunk_start in range(0, len(uncached_states), MAX_STATES_PER_CHUNK):
                chunk_end = min(chunk_start + MAX_STATES_PER_CHUNK, len(uncached_states))
                chunk_states = uncached_states[chunk_start:chunk_end]
                
                chunk_result = self._evaluate_chunk(chunk_states)
                chunk_scores.extend(chunk_result)
                
                # Cache results immediately
                for state, score in zip(chunk_states, chunk_result):
                    self.eval_cache[self.canonical_state_key(state)] = score
            
            # Fill in the uncached scores in the original order
            for idx, score in zip(uncached_indices, chunk_scores):
                scores[idx] = score
        
        return scores
    
    def _evaluate_chunk(self, states: List[str]) -> List[float]:
        """
        Evaluate a single chunk of states.
        
        Uses either LLM-based evaluation or heuristic scoring depending on configuration.
        """
        # Use heuristic scoring if enabled (ablation study variant)
        if self.use_heuristic_scoring:
            return self._evaluate_chunk_heuristic(states)
        
        # Default: LLM-based evaluation
        prompt = self.get_state_eval_prompt(self.input_seq, states)
        # Evaluation output is short: ~30 chars per state ("SUMMARY_X: R=0.X I=0.X C=0.X")
        eval_max_tokens = max(64, len(states) * 40)
        state_evals = self.chat_completions(
            prompt,
            temperature=self.eval_temperature,
            max_tokens=eval_max_tokens,
            n=self.n_evals,
            llm=self.llm_evaluation,
            do_sample=self.do_sample,
        )

        print("\n[DEBUG] Raw LLM evaluation outputs:")
        for i, s in enumerate(state_evals):
            print(f"--- sample {i} ---")
            print(s)
            print("---------------")

        vote_results = self.heuristic_calculator(states, state_evals)
        return vote_results
    
    def _evaluate_chunk_heuristic(self, states: List[str]) -> List[float]:
        """
        Evaluate states using heuristic scorer instead of LLM.
        
        Computes R, I, C scores directly from selected triples and combines them
        using the configured semantic weights.
        
        Args:
            states: List of state strings (each is newline-separated triple indices)
            
        Returns:
            List of composite scores (R*w_r + I*w_i + C*w_c)
        """
        scores = []
        
        for state in states:
            # Parse state (newline-separated triple indices)
            if not state.strip():
                # Empty state = no triples selected
                scores.append(0.0)
                continue
            
            try:
                # Extract selected triple indices from state
                selected_indices = []
                for line in state.strip().split('\n'):
                    if line.strip().isdigit():
                        idx = int(line.strip())
                        if 1 <= idx <= len(self.input_seq.split('\n')):
                            selected_indices.append(idx - 1)  # Convert to 0-indexed
                
                if not selected_indices:
                    scores.append(0.0)
                    continue
                
                # Get the actual selected triples
                all_triples_list = [t.strip() for t in self.input_seq.split('\n') if t.strip()]
                selected_triples = [all_triples_list[i] for i in selected_indices 
                                  if i < len(all_triples_list)]
                
                # Compute individual scores
                r_scores = []
                i_scores = []
                c_scores = []
                
                for triple in selected_triples:
                    r_scores.append(self.heuristic_scorer.score_relatedness(triple))
                    i_scores.append(self.heuristic_scorer.score_informativeness(triple))
                    c_scores.append(self.heuristic_scorer.score_coverage(triple, selected_triples))
                
                # Average the scores
                avg_r = sum(r_scores) / len(r_scores) if r_scores else 0.0
                avg_i = sum(i_scores) / len(i_scores) if i_scores else 0.0
                avg_c = sum(c_scores) / len(c_scores) if c_scores else 0.0
                
                # Combine using semantic weights
                composite_score = (self.w_relatedness * avg_r + 
                                 self.w_informativeness * avg_i + 
                                 self.w_coverage * avg_c)
                scores.append(min(1.0, max(0.0, composite_score)))
                
                if len(states) <= 5:  # Only print debug for small batches
                    print(f"[HEURISTIC] State: {', '.join(map(str, selected_indices))} | "
                          f"R={avg_r:.3f}, I={avg_i:.3f}, C={avg_c:.3f} | "
                          f"Score={composite_score:.3f}")
            
            except Exception as e:
                print(f"[WARNING] Error evaluating state: {e}")
                scores.append(0.0)
        
        print(f"\n[HEURISTIC SCORING] Evaluated {len(states)} states")
        return scores

    def bfs(self, verbose: bool = True, event_callback: Optional[Callable[[Dict[str, Any]], None]] = None) -> str:
        """
        Perform task-decomposed BFS search.
        
        At each step, generates candidates from three task-specific prompts,
        then evaluates and prunes based on combined criteria.
        """
        queue = deque()
        queue.append(self.root)
        seen_state_keys = {self.canonical_state_key(self.root.state)}
        if event_callback:
            event_callback({"type": "start", "step": 0, "total_steps": self.n_steps, "state": ""})

        for step in range(1, self.n_steps + 1):
            if event_callback:
                event_callback({"type": "step_start", "step": step, "total_steps": self.n_steps})
            if verbose:
                print(f"\n{'='*70}")
                print(f"Step {step} / {self.n_steps}")
                print('='*70)

            current_layer_size = len(queue)
            if current_layer_size == 0:
                if verbose:
                    print("Queue is empty; stopping early.")
                break

            # Expand all nodes in current layer
            new_nodes = deque()
            thought_gen_start = time.time()
            for i in range(current_layer_size):
                node = queue.popleft()

                if event_callback:
                    event_callback({
                        "type": "expand",
                        "step": step,
                        "state": node.state,
                        "triple_ids": node.get_triple_ids(),
                    })

                if verbose:
                    print(f"\n--- Expanding node {i + 1}/{current_layer_size} ---")
                    print(f"Current state: {node}")
                    if node.state:
                        print(f"Triple IDs: {node.state}")

                # Generate thoughts from all three tasks
                all_task_thoughts = self.generate_thoughts_all_tasks(node.state, verbose)
                if event_callback:
                    event_callback({
                        "type": "thoughts",
                        "step": step,
                        "state": node.state,
                        "tasks": all_task_thoughts,
                    })

                # Parse current state to avoid duplicates
                existing_ids = set()
                if node.state.strip():
                    existing_ids = {
                        int(x) for x in node.state.strip().splitlines()
                        if x.strip().isdigit()
                    }

                # Combine thoughts from all tasks
                all_thoughts = []
                for task_name, thoughts in all_task_thoughts.items():
                    all_thoughts.extend(thoughts)
                
                # Remove duplicates while preserving order
                all_thoughts = list(dict.fromkeys(all_thoughts))
                
                if verbose:
                    print(f"\nCombined unique thoughts: {all_thoughts}")

                # Create children
                children_created = 0
                duplicates_skipped = 0
                for t_str in all_thoughts:
                    try:
                        t_id = int(t_str)
                    except ValueError:
                        continue

                    if t_id in existing_ids:
                        continue

                    new_state = t_str if node.state == "" else node.state + "\n" + t_str

                    if not self.validate_state(new_state):
                        if verbose:
                            print(f"WARNING: Invalid state, skipping: {new_state}")
                        continue

                    canonical_key = self.canonical_state_key(new_state)
                    if canonical_key in seen_state_keys:
                        # Equivalent paths are one candidate because state identity
                        # is order-insensitive.
                        duplicates_skipped += 1
                        if verbose:
                            print(f"DEDUP: skipping duplicate combination {canonical_key} (already produced this step)")
                        continue

                    child = TreeNode(
                        state=new_state,
                        thought=t_str,
                        depth=node.depth + 1,
                        parent=node
                    )
                    node.children.append(child)
                    new_nodes.append(child)
                    seen_state_keys.add(canonical_key)
                    children_created += 1

                if verbose:
                    print(f"Children created: {children_created}")
                    if duplicates_skipped:
                        print(f"Duplicates skipped: {duplicates_skipped}")
                    if children_created == 0:
                        print("WARNING: No valid children - branch exhausted")
                if event_callback:
                    event_callback({
                        "type": "children",
                        "step": step,
                        "states": [child.state for child in list(node.children) if child.depth == node.depth + 1],
                    })

            thought_gen_time = time.time() - thought_gen_start

            queue = new_nodes

            if len(queue) == 0:
                if verbose:
                    print("\nNo nodes left after expansion; breaking.")
                break

            # Evaluate all states
            eval_time = 0.0
            if self.no_scoring_mode == "greedy":
                if verbose:
                    print(f"\n--- No-scoring mode active ({self.no_scoring_mode}) ---")
                    print("Skipping evaluation; keeping candidates in generation order.")
                for node in queue:
                    node.value = 0.0
            else:
                if verbose:
                    print(f"\n--- Evaluating {len(queue)} states ---")

                eval_start = time.time()
                states = [node.state for node in queue]
                values = self.state_evaluator(states)
                eval_time = time.time() - eval_start
                for node, val in zip(queue, values):
                    node.value = val
                if event_callback:
                    event_callback({
                        "type": "evaluated",
                        "step": step,
                        "states": states,
                        "scores": values,
                    })

            if verbose:
                print(f"\n⏱  Step {step} timing: thought_gen={thought_gen_time:.1f}s, eval={eval_time:.1f}s, total={thought_gen_time+eval_time:.1f}s")

            if verbose:
                print("\nState evaluations:")
                for idx, node in enumerate(queue):
                    print(f"  State {idx}: value={node.value:.4f}, depth={node.depth}, triples={len(node.get_triple_ids())}")

            # Prune more softly during intermediate steps so a single noisy score
            # does not eliminate a promising branch too early.
            keep_k = 1 if step == self.n_steps else min(
                len(queue),
                max(1, int(round(self.breadth_limit * self.prune_keep_multiplier))),
            )
            if verbose:
                print(f"\nPruning to top {keep_k}...")

            if self.no_scoring_mode == "greedy":
                queue = deque(list(queue)[:keep_k])
            else:
                # Sort by value (descending), then by triple ID sum (ascending) for deterministic tie-breaking
                sorted_nodes = sorted(
                    queue,
                    key=lambda n: (-n.value, sum(n.get_triple_ids()) if n.get_triple_ids() else float('inf')),
                    reverse=False
                )
                top_nodes = sorted_nodes[:keep_k]

                # Use sorted order directly instead of preserving original queue order
                queue = deque(top_nodes)

            if event_callback:
                event_callback({
                    "type": "pruned",
                    "step": step,
                    "states": [node.state for node in queue],
                    "keep": keep_k,
                })

            if verbose:
                print(f"Queue size after pruning: {len(queue)}")

        if not queue:
            if verbose:
                print("\nSearch finished with empty queue; returning root state.")
            if event_callback:
                event_callback({
                    "type": "complete",
                    "step": self.n_steps,
                    "state": self.root.state,
                    "triple_ids": self.root.get_triple_ids(),
                })
            return self.root.state

        # Best node
        if self.no_scoring_mode == "greedy":
            best_node = queue[0]
        else:
            best_node = max(queue, key=lambda n: n.value)
        if verbose:
            print("\n" + "="*70)
            print("SEARCH COMPLETE (Task-Decomposed)")
            print("="*70)
            print("Best state summary:")
            print(f"  Value: {best_node.value:.4f}")
            print(f"  Depth: {best_node.depth}")
            print(f"  Triple count: {len(best_node.get_triple_ids())}")
            print(f"  Selected triple IDs: {best_node.state}")
            print("="*70)

        if event_callback:
            event_callback({
                "type": "complete",
                "step": self.n_steps,
                "state": best_node.state,
                "triple_ids": best_node.get_triple_ids(),
            })

        return best_node.state

    def dfs(self, verbose: bool = True) -> str:
        """
        Perform depth-first search with task decomposition.
        
        DFS explores paths to maximum depth using multi-task thought generation
        before backtracking. Selects best thoughts at each step.
        
        Args:
            verbose: Whether to print detailed progress
            
        Returns:
            Best state string found
        """
        best_node = self.root
        best_value = float('-inf')
        
        def dfs_recursive(node: TreeNode, depth: int) -> None:
            nonlocal best_node, best_value
            
            # Terminal condition
            if depth >= self.n_steps:
                if self.no_scoring_mode == "greedy":
                    value = 0.0
                else:
                    value = self.state_evaluator([node.state])[0]
                node.value = value
                
                if verbose:
                    print(f"\n[LEAF] Depth {depth}, Value: {value:.4f}, State: {node.state}")
                
                if value > best_value:
                    best_value = value
                    best_node = node
                    if verbose:
                        print(f"  ★ New best! Value={value:.4f}")
                return
            
            if verbose:
                print(f"\n{'  ' * depth}[DFS Step {depth + 1}/{self.n_steps}]")
                print(f"{'  ' * depth}Current state: {node.state if node.state else '<root>'}")
            
            # Generate thoughts from all tasks
            all_task_thoughts = self.generate_thoughts_all_tasks(node.state, verbose=False)
            
            # Parse existing IDs
            existing_ids = set()
            if node.state.strip():
                existing_ids = {
                    int(x) for x in node.state.strip().splitlines()
                    if x.strip().isdigit()
                }
            
            # Combine all thoughts
            all_thoughts = []
            for task_name, thoughts in all_task_thoughts.items():
                all_thoughts.extend(thoughts)
            all_thoughts = list(dict.fromkeys(all_thoughts))
            
            if verbose:
                print(f"{'  ' * depth}Generated {len(all_thoughts)} thoughts from all tasks")
            
            # Create candidates
            candidates = []
            for t_str in all_thoughts:
                try:
                    t_id = int(t_str)
                except ValueError:
                    continue
                
                if t_id in existing_ids:
                    continue
                
                new_state = t_str if node.state == "" else node.state + "\n" + t_str
                
                if not self.validate_state(new_state):
                    continue
                
                child = TreeNode(
                    state=new_state,
                    thought=t_str,
                    depth=node.depth + 1,
                    parent=node
                )
                candidates.append(child)
            
            if not candidates:
                if verbose:
                    print(f"{'  ' * depth}No valid children - backtracking")
                return
            
            if self.no_scoring_mode == "greedy":
                # No-scoring ablation: preserve generation order, take first child only.
                first_child = candidates[0]
                first_child.value = 0.0
                sorted_candidates = [(first_child, 0.0)]
            else:
                # Evaluate candidates
                candidate_states = [c.state for c in candidates]
                candidate_values = self.state_evaluator(candidate_states)

                for child, value in zip(candidates, candidate_values):
                    child.value = value

                # Sort by value (best first)
                sorted_candidates = sorted(
                    zip(candidates, candidate_values),
                    key=lambda x: x[1],
                    reverse=True
                )
            
            if verbose:
                print(f"{'  ' * depth}Candidate evaluations:")
                for i, (child, val) in enumerate(sorted_candidates[:3]):
                    print(f"{'  ' * depth}  {i+1}. Thought={child.thought}, Value={val:.4f}")
            
            # Explore best candidates
            for child, value in sorted_candidates:
                node.children.append(child)
                
                if verbose:
                    print(f"{'  ' * depth}→ Exploring thought={child.thought} (value={value:.4f})")
                
                dfs_recursive(child, depth + 1)
        
        if verbose:
            print("="*70)
            print("DEPTH-FIRST SEARCH (Task-Decomposed DFS)")
            print("="*70)
        
        dfs_recursive(self.root, 0)
        
        if verbose:
            print("\n" + "="*70)
            print("DFS COMPLETE")
            print("="*70)
            print("Best state found:")
            print(f"  Value: {best_value:.4f}")
            print(f"  Depth: {best_node.depth}")
            print(f"  Triple count: {len(best_node.get_triple_ids())}")
            print(f"  Selected triple IDs: {best_node.state}")
            print("="*70)
        
        return best_node.state

    def __repr__(self) -> str:
        return (
            f"TaskDecomposedToT(n_steps={self.n_steps}, "
            f"n_candidates_per_task={self.n_candidates_per_task}, "
            f"breadth_limit={self.breadth_limit}, "
            f"thought_temperature={self.thought_temperature}, "
            f"eval_temperature={self.eval_temperature}, "
            f"do_sample={self.do_sample}, "
            f"no_scoring_mode={self.no_scoring_mode})"
        )

