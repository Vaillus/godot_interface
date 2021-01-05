import socket
import json
import numpy as np
import os
import subprocess
import ast
import utils


class GodotEnvironment:
    def __init__(self, params={}):

        self.host = None
        self.port = None

        self.godot_path_str = None
        self.env_path_str = None

        self.socket = None
        self.client_socket = None

        self.godot_process = None
        self.is_godot_launched = False
        self.is_rendering = True

        self.agent_names = None
        self.state_min = None
        self.state_max = None

        self.display_actions = None
        self.display_states = None
        self.verbose = None
        self.seed = None
        self.random_generator = None

        self.set_params_from_dict(params)

        self.set_other_params()

    def set_params_from_dict(self, params={}):
        self.host = params.get("host", '127.0.0.1')
        self.port = params.get("port", 4242)
        self.godot_path_str = params.get("godot path", "")
        self.env_path_str = params.get("environment path", "")
        self.agent_names = params.get("agent names", [])
        self.state_min = np.array(params.get("state min", [0, 0]))
        self.state_max = np.array(params.get("state min", [1000, 1000]))
        self.display_actions = params.get("display actions", False)
        self.display_states = params.get("display states", False)
        self.verbose = params.get('verbose', False)
        self.seed = params.get('seed', np.random.randint(0, 1e5))

    def set_other_params(self):
        self.random_generator = np.random.RandomState(seed=self.seed)

    # main functions ===================================================================================================

    def reset(self, render):
        """
        Initialize the environment and returns its first state.
        To do so, it:
        - handles the rendering type
        - Creates a godot simulation instance in a subprocess if it is needed
        - Creates a tcp connexion with the simulation
        - Gets the initial state of the environment through the tcp connection
        - Scale the state
        :param render: boolean, indicates whether the simulator displays, - in which case the game executes 
        at normal speed - or not - the game executes at a higher rate (max 17 times faster, for now)
        :return: initial state of the environment (dictionary)
        """
        # change render type and end simulation to restart it with the right parameter later if specified so.
        self._change_render_type_if_needed(render)

        # Initializing a subprocess where a godot instance is launched, if it doesn't exist yet.
        self._launch_simulation_if_needed()

        # Initializing the socket if it's not already done.
        if self.socket is None:
            self._initialize_socket()
            # Creating the connexion with the simulator
            self._wait_for_connection()

        # Send the first request to get the initial state of the simulation
        first_request = self._create_request(initialization=True)
        self.client_socket.sendall(first_request)

        # Get the first state of the simulation, scale it and return it
        env_data = self._get_environment_state()
        if self.display_states:
            print(env_data)
        states_data = env_data["agents_data"]
        #states_data = self.scale_states_data(states_data)

        return states_data

    def step(self, actions_data):
        """
        sending an action to the godot agent and returns the reward it earned, the new state of the environment and a
        boolean indicating whether the game is done.
        :param action_data: dictionary
        :return:states_data (dic), rewards_data (dic), done (boolean), n_frames (int)
        """
        # prepare and send data to simulation
        request = self._create_request(agents_data=actions_data)
        if self.display_actions:
            print(request)
        self.client_socket.sendall(request)

        # receive environment data
        env_data = self._get_environment_state()
        if self.display_states:
            print(env_data)

        # splitting data
        states_data, rewards_data = self._split_env_data(env_data["agents_data"])

        n_frames = env_data["n_frames"]
        # scaling reward
        for n_agent in range(len(rewards_data)):
            rewards_data[n_agent]["reward"] /= n_frames
        # scaling states
        # states_data = self.scale_states_data(states_data)

        # handling ending condition
        done = env_data["done"]
        if done:
            pass
            # self._end_connection() # I used to do it.
            #self.close()


        return states_data, rewards_data, done, n_frames

    def close(self):
        """Properly closes the environment and the connection"""
        # print(self.godot_process.poll())

        termination_request = self._create_request(termination=True)
        self.client_socket.sendall(termination_request)
        self._end_connection()
        # self.godot_process.kill()

        self.godot_process.wait()
        self.is_godot_launched = False

    # Connection functions =============================================================================================

    def _initialize_socket(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def _end_connection(self):
        """Closes the socket, and then reset it. Also reset client socket."""
        self.socket.close()
        self.socket = None
        self.client_socket = None

    def _wait_for_connection(self):
        """
        Runs until a connection is made
        :return:
        """
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind((self.host, self.port))
        self.socket.listen()
        self.client_socket, addr = self.socket.accept()
        if self.verbose:
            print('Connected by', addr)

    def _create_request(self, initialization=False, termination=False, agents_data=None):
        """
        Handles the type of request to be sent and shape the request into the correct form.
        :param initialization: boolean, indicates if the request must be in the form of an initialization request.
        :param termination: boolean, indicates if the request must be in the form of an termination request.
        :param agents_data: list of dictionaries contaning the fields "name" (string) and "action" (int) the value of
        the action to be taken by the actor.
        :return: The request is a dictionary stored into a string, ready to be sent to the simulator
        """
        request = {}
        request["initialization"] = initialization
        if initialization:
            request["seed"] = self.random_generator.randint(low=0, high=1e6)
        request["termination"] = termination
        request["render"] = self.is_rendering
        if initialization == False and termination == False:
            request["agents_data"] = self._format_actions_data(agents_data)
        request = json.dumps(request).encode()
        return request

    def _format_actions_data(self, agents_data):
        """
        formats agents data to the correct shape
        :param agents_data: list of dictionaries
        :return: list of dictionaries
        """
        for n_agent in range(len(agents_data)):
            # convert the actions to the correct type
            if isinstance(agents_data[n_agent]["action"], np.integer):
                agents_data[n_agent]["action"] = int(agents_data[n_agent]["action"])
        return agents_data

    def _wait_and_receive_states_data(self):
        """
        wait until it receives environment data from Godot simulation
        :return: list of dictionaries
        """
        states_data = None
        condition = False
        # stay in the loop until data is received
        while condition != True:
            states_data = self.client_socket.recv(10000)
            states_data = states_data.decode()
            # checking if the length of the data is enough to be considered valid
            if len(states_data) > 4:
                condition = True
        return states_data

    def _format_states_data(self, state_data):
        """
        returns formatted states data
        :param state_data: list of dictionaries
        :return: list of dictionaries
        """
        state_data = json.loads(state_data)
        for n_agent, agent_data in enumerate(state_data["agents_data"]):
            if isinstance(agent_data['state'], str):
                state_data["agents_data"][n_agent]["state"] = ast.literal_eval(agent_data["state"])
        return state_data

    def _get_environment_state(self):
        """
        wait and receive states data and format it to the correct shape
        :return: list of dictionaries
        """
        states_data = self._wait_and_receive_states_data()
        states_data = self._format_states_data(states_data)
        return states_data





    def _change_render_type_if_needed(self, render):
        """
        Handling the case where we changed te rendering type and the godot engine is launched (not the first time the
        class is used). We want to close the godot session and create a new one with a different rendering parameter.
        :param render: bool
        :return:
        """
        if (render != self.is_rendering) and self.is_godot_launched:
            if self.socket is None:
                self._initialize_socket()
            self._wait_for_connection()

            self.close()
        self.is_rendering = render

    def _launch_simulation_if_needed(self):
        if not self.is_godot_launched:
            self.godot_path_str = utils.get_path(self.godot_path_str, add_absolute=True)
            self.env_path_str = utils.get_path(self.env_path_str) 
            print(self.env_path_str)
            print(self.godot_path_str)
            command = "{} --main-pack {}".format(self.godot_path_str, self.env_path_str)
            if not self.is_rendering:
                command = command + " --disable-render-loop --no-window"
            self.godot_process = subprocess.Popen(command, shell=True)
            self.is_godot_launched = True

    def _split_env_data(self, agents_data):
        """
        Split the data received by the environment in two lists. One containing rewards and the other containing the
        states
        :param agents_data: list of directories
        :return: two lists of directories
        """
        states_data = []
        rewards_data = []
        for agent_data in agents_data:
            state_data = {"name": agent_data["name"], "state": agent_data["state"]}
            states_data.append(state_data)
            reward_data = {"name": agent_data["name"], "reward": agent_data["reward"]}
            rewards_data.append(reward_data)
        return states_data, rewards_data

    def scale_states_data(self, states_data):
        """
        Scale states data in a dictionary
        :param states_data: dictionary
        :return: dictionary
        """
        for state_id, state_data in enumerate(states_data):
            state = state_data["state"]
            state = self.scale_state(state)
            states_data[state_id]["state"] = state
        return states_data

    def scale_state(self, state):
        """ Scale a single state (np array)"""
        scaled_state = (state - self.state_min) / (self.state_max - self.state_min)
        return scaled_state

