//! MiniMax-powered draft generation: client, prompts, workflows.

pub mod agent;
pub mod client;
pub mod projects;
pub mod prompts;
pub mod workflow;

pub use agent::{AgentDraftWorkflow, AgentWorkflowResult};
pub use client::{AiClient, DraftGenerationError};
pub use workflow::{DraftWorkflow, WorkflowResult};