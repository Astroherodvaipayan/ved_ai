import { BookOpen, Brain, FileText, MessageCircle, Mic } from "lucide-react";
import { useEffect } from "react";
import AskTutorTab from "./AskTutorTab";
import LoadingState from "./LoadingState";
import PracticeZoneTab from "./PracticeZoneTab";
import SummaryTab from "./SummaryTab";
import TalkToTutorMode from "./TalkToTutorMode";
import TranscriptionTab from "./TranscriptionTab";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

// Helper function to format time in MM:SS format
const formatTime = (seconds) => {
	const mins = Math.floor(seconds / 60);
	const secs = Math.floor(seconds % 60);
	return `${mins.toString().padStart(2, "0")}:${secs
		.toString()
		.padStart(2, "0")}`;
};

export default function OutputSection({
	data,
	activeTab,
	setActiveTab,
	onRetry,
	chatMessages,
	setChatMessages,
	inputType,
	topic,
}) {
	// If we're switching away from transcription tab and input is PDF, go to summary
	useEffect(() => {
		if (inputType === "pdf" && activeTab === "transcription") {
			setActiveTab("summary");
		}
	}, [inputType, activeTab, setActiveTab]);

	// Get tab icon based on tab value
	const getTabIcon = (tabValue) => {
		switch (tabValue) {
			case "transcription":
				return <FileText className='w-4 h-4 md:w-5 md:h-5' />;
			case "summary":
				return <BookOpen className='w-4 h-4 md:w-5 md:h-5' />;
			case "quiz":
				return <Brain className='w-4 h-4 md:w-5 md:h-5' />;
			case "ask":
				return <MessageCircle className='w-4 h-4 md:w-5 md:h-5' />;
			case "talk":
				return <Mic className='w-4 h-4 md:w-5 md:h-5' />;
			default:
				return null;
		}
	};

	// Get tab label based on tab value
	const getTabLabel = (tabValue) => {
		switch (tabValue) {
			case "transcription":
				return "Transcription";
			case "summary":
				return "Smart Notes";
			case "quiz":
				return "Practice Zone";
			case "ask":
				return "Ask ved-ai";
			case "talk":
				return "Talk to Tutor";
			default:
				return "";
		}
	};

	// Define available tabs based on input type
	const availableTabs =
		inputType === "pdf"
			? ["summary", "quiz", "ask", "talk"]
			: ["transcription", "summary", "quiz", "ask", "talk"];

	return (
		<div className='h-full flex flex-col'>
			<div className='mb-2 md:mb-4'>
				<h4 className='text-base md:text-xl font-semibold text-emerald-800 mb-3 md:mb-4'>
					{topic.replace(/\.pdf|\.mp3$/, "") || "Learning Suite"}
				</h4>

				{/* Mobile Tab Selector */}
				<div className='md:hidden mb-3'>
					<select
						value={activeTab}
						onChange={(e) => setActiveTab(e.target.value)}
						className='w-full p-2 rounded-md bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500'
					>
						{availableTabs.map((tab) => (
							<option key={tab} value={tab}>
								{getTabLabel(tab)}
							</option>
						))}
					</select>
				</div>

				{/* Desktop Tabs */}
				<div className='hidden md:block w-full overflow-x-auto pb-2'>
					<Tabs
						defaultValue={activeTab}
						value={activeTab}
						onValueChange={setActiveTab}
					>
						<TabsList className='w-full'>
							{availableTabs.map((tab) => (
								<TabsTrigger
									key={tab}
									value={tab}
									className='flex-1 text-xs md:text-sm whitespace-nowrap'
								>
									{getTabIcon(tab)}
									<span className='ml-2'>{getTabLabel(tab)}</span>
								</TabsTrigger>
							))}
						</TabsList>
					</Tabs>
				</div>
			</div>

			<div className='flex-1 bg-white/30 backdrop-blur-lg rounded-xl md:rounded-2xl shadow-lg md:shadow-xl p-3 md:p-6 border border-white/40'>
				{/* Mobile Tab Indicator */}
				<div className='md:hidden flex items-center mb-3 text-emerald-700'>
					{getTabIcon(activeTab)}
					<span className='ml-2 font-medium'>{getTabLabel(activeTab)}</span>
				</div>

				{/* Loading State */}
				{data.loading && <LoadingState />}

				{/* Content Tabs - Only show when not loading */}
				{!data.loading && (
					<Tabs
						value={activeTab}
						onValueChange={setActiveTab}
						className='h-full flex flex-col'
					>
						<div className='sr-only'>
							<TabsList>
								{availableTabs.map((tab) => (
									<TabsTrigger key={tab} value={tab}>
										{getTabIcon(tab)}
										<span className='ml-2'>{getTabLabel(tab)}</span>
									</TabsTrigger>
								))}
							</TabsList>
						</div>

						<div className='flex-1'>
							{inputType !== "pdf" && (
								<TabsContent value='transcription' className='h-full'>
									<TranscriptionTab data={data} onRetry={onRetry} />
								</TabsContent>
							)}

							<TabsContent value='summary' className='h-full'>
								<SummaryTab data={data} />
							</TabsContent>

							<TabsContent value='quiz' className='h-full'>
								<PracticeZoneTab data={data} />
							</TabsContent>

							<TabsContent value='ask' className='h-full'>
								<AskTutorTab
									data={data}
									messages={chatMessages}
									setMessages={setChatMessages}
								/>
							</TabsContent>

							<TabsContent value='talk' className='h-full'>
								<TalkToTutorMode
									data={data}
									topic={topic.replace(/\.pdf|\.mp3$/, "")}
								/>
							</TabsContent>
						</div>
					</Tabs>
				)}
			</div>
		</div>
	);
}
